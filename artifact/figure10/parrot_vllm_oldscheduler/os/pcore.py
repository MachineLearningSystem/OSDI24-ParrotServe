# Copyright (c) 2023 by Microsoft Corporation.
# Licensed under the MIT license.


from typing import Dict, List
import asyncio
import time
from dataclasses import asdict

from parrot_vllm_oldscheduler.program.function import SemanticCall, NativeCall
from parrot_vllm_oldscheduler.utils import RecyclePool
from parrot_vllm_oldscheduler.constants import (
    PROCESS_POOL_SIZE,
    ENGINE_POOL_SIZE,
    OS_LOOP_INTERVAL,
    VM_EXPIRE_TIME,
    ENGINE_EXPIRE_TIME,
)
from parrot_vllm_oldscheduler.protocol.layer_apis import ping_engine
from parrot_vllm_oldscheduler.protocol.runtime_info import (
    VMRuntimeInfo,
    EngineRuntimeInfo,
)
from parrot_vllm_oldscheduler.engine.config import EngineConfig
from parrot_vllm_oldscheduler.utils import get_logger, cprofile
from parrot_vllm_oldscheduler.exceptions import ParrotOSUserError, ParrotOSInteralError

from .config import OSConfig
from .process.process import Process
from .memory.mem_space import MemorySpace
from .engine import ExecutionEngine
from .thread_dispatcher import DispatcherConfig, ThreadDispatcher
from .tokenizer import Tokenizer


logger = get_logger("PCore")


class PCore:
    """Parrot OS Core. It's the entry of the OS-layer of the parrot runtime system.

    It manages the following components:
    - Multiple processes in the frontend.
    - Multiple engines in the backend.
    - Memory space.
    - Thread dispatcher.
    - Tokenizer.
    """

    def __init__(self, os_config: Dict):
        # ---------- Config ----------
        dispatcher_config = os_config.pop("dispatcher")
        dispatcher_config = DispatcherConfig(**dispatcher_config)
        self.os_config = OSConfig(**os_config)

        if self.os_config.max_proc_num > PROCESS_POOL_SIZE:
            logger.warning(
                f"Config max_proc_num: {self.os_config.max_proc_num} larger than "
                "proc_pool_size: {PROCESS_POOL_SIZE}"
            )
        if self.os_config.max_engines_num > ENGINE_POOL_SIZE:
            logger.warning(
                f"Config max_engines_num: {self.os_config.max_engines_num} larger than "
                "engine_pool_size: {ENGINE_POOL_SIZE}"
            )

        # ---------- Components ----------
        self.processes: Dict[int, Process] = {}  # pid -> process
        self.engines: Dict[int, ExecutionEngine] = {}  # engine_id -> engine
        self.mem_space = MemorySpace()

        def _ping_engine_method(engine: ExecutionEngine):
            self._ping_engine(engine)

        self.dispatcher = ThreadDispatcher(
            config=dispatcher_config,
            engines=self.engines,
            ping_engine_method=_ping_engine_method,
        )
        self.tokenizer = Tokenizer()

        # ---------- Id Allocator ----------
        self.pid_pool = RecyclePool(PROCESS_POOL_SIZE)
        self.engine_pool = RecyclePool(ENGINE_POOL_SIZE)

        # ---------- Last Seen Time ----------
        self.proc_last_seen_time: Dict[int, float] = {}  # pid -> last_seen_time
        self.engine_last_seen_time: Dict[int, float] = {}  # engine_id -> last_seen_time

        logger.info(
            f"PCore started with config: \n"
            + "\n".join(
                [f"  {key}={value}, " for key, value in self.os_config.__dict__.items()]
            )
        )

    def _check_expired(self):
        cur_time = time.perf_counter_ns()

        # VMs
        for pid, last_seen_time in self.proc_last_seen_time.items():
            if (cur_time - last_seen_time) / 1e9 > VM_EXPIRE_TIME:
                self.processes[pid].dead = True

        # Engines
        for engine_id, last_seen_time in self.engine_last_seen_time.items():
            if (cur_time - last_seen_time) / 1e9 > ENGINE_EXPIRE_TIME:
                self.engines[engine_id].dead = True

    def _ping_engine(self, engine: ExecutionEngine):
        if not engine.dead:
            resp = ping_engine(engine.http_address)
            if resp.pong:
                engine.runtime_info = EngineRuntimeInfo(**resp.runtime_info)
                # logger.debug(
                #     "Ping engine success. Runtime info: \n"
                #     + engine.runtime_info.display()
                # )
            else:
                engine.dead = True

    def _sweep_dead_clients(self):
        dead_procs: List[Process] = [
            proc for proc in self.processes.values() if proc.dead
        ]
        dead_engines: List[ExecutionEngine] = [
            engine for engine in self.engines.values() if engine.dead
        ]

        # VMs
        for process in dead_procs:
            pid = process.pid
            self.processes.pop(pid)
            # If a VM is dead, we need to free all its resources (garbage collection).
            process.free_process()
            self.proc_last_seen_time.pop(pid)
            self.pid_pool.free(pid)
            logger.info(f"VM (pid={pid}) disconnected.")

        # Engines
        for engine in dead_engines:
            engine_id = engine.engine_id
            self.engines.pop(engine_id)
            self.engine_last_seen_time.pop(engine_id)
            self.engine_pool.free(engine_id)
            logger.info(f"Engine {engine.name} (id={engine_id}) disconnected.")

    def _check_process(self, pid: int):
        if pid not in self.processes:
            raise ParrotOSUserError(ValueError(f"Unknown pid: {pid}"))

        process = self.processes[pid]
        if process.dead:
            logger.error(f"Process (pid={pid}) is dead. Raise exception.")
            raise ParrotOSUserError(RuntimeError(f"Process (pid={pid}) is dead."))

        if process.bad:
            process.dead = True
            logger.error(
                f"Process (pid={pid}) is bad. Raise exception: {process.bad_exception.args}."
            )
            raise process.bad_exception

    # ---------- Public APIs ----------

    def register_vm(self) -> int:
        """Register a new VM as a process in the OS."""
        pid = self.pid_pool.allocate()
        process = Process(
            pid=pid,
            dispatcher=self.dispatcher,
            memory_space=self.mem_space,
            tokenizer=self.tokenizer,
        )
        self.processes[pid] = process
        self.proc_last_seen_time[pid] = time.perf_counter_ns()
        logger.info(f"VM (pid={pid}) registered.")
        return pid

    def register_engine(self, config: EngineConfig) -> int:
        """Register a new engine in the OS."""
        engine_id = self.engine_pool.allocate()
        engine = ExecutionEngine(
            engine_id=engine_id,
            config=config,
        )
        self.engines[engine_id] = engine
        self.engine_last_seen_time[engine_id] = time.perf_counter_ns()
        logger.debug(f"Engine {engine.name} (id={engine_id}) registered.")
        return engine_id

    def vm_heartbeat(self, pid: int) -> Dict:
        """Update the last seen time of a VM, and return required data."""

        self._check_process(pid)

        self.proc_last_seen_time[pid] = time.perf_counter_ns()

        mem_used = self.mem_space.profile_process_memory(pid)
        num_total_tokens = self.mem_space.profile_process_tokens(pid)
        num_threads = len(self.processes[pid].threads)

        vm_runtime_info = VMRuntimeInfo(
            mem_used=mem_used,
            num_total_tokens=num_total_tokens,
            num_threads=num_threads,
        )

        logger.debug(
            f"VM (pid={pid}) heartbeat received. Profiled status: \n"
            + vm_runtime_info.display()
        )

        return asdict(vm_runtime_info)

    def engine_heartbeat(
        self,
        engine_id: int,
        engine_runtime_info: EngineRuntimeInfo,
    ):
        """Update the last seen time of an engine and other engine info."""

        if engine_id not in self.engines:
            raise ParrotOSUserError(ValueError(f"Unknown engine_id: {engine_id}"))

        engine = self.engines[engine_id]
        self.engine_last_seen_time[engine_id] = time.perf_counter_ns()
        engine.runtime_info = engine_runtime_info
        logger.debug(
            f"Engine {engine.name} (id={engine_id}) heartbeat received. "
            "Runtime info: \n" + engine_runtime_info.display()
        )

    def submit_native_call(self, pid: int, call: NativeCall) -> int:
        """Submit a native call from a VM to the OS."""

        # The native call must be a short, executable and stateless call. (FaaS)
        # The native call will be executed immediately once all its inputs are ready.

        self._check_process(pid)
        process = self.processes[pid]

        # Rewrite the call using namespace
        process.rewrite_call(call)

        # Execute it immediately
        process.execute_native_call(call)

    def submit_semantic_call(self, pid: int, call: SemanticCall) -> int:
        """Submit a semantic call from a VM to the OS."""

        # Submit call will only put the call into a Queue, and the call will be executed later.
        # This is for get the partial DAG and do optimized scheduling.

        # NOTE(chaofan): For stateful call, since the queue is FIFO, the state contexts are
        # correctly maintained.

        self._check_process(pid)
        process = self.processes[pid]

        # Rewrite the call using namespace
        process.rewrite_call(call)

        # Convert it to a "Thread"
        thread = process.make_thread(call)

        # Push it to the dispatcher
        self.dispatcher.push_thread(thread)

        logger.info(
            f'Function call "{call.func.name}" submitted from VM (pid={pid}). '
            f"Created thread: tid={thread.tid}"
        )

    async def placeholder_set(self, pid: int, placeholder_id: int, content: str):
        """Set a placeholder content from VM."""

        self._check_process(pid)

        process = self.processes[pid]
        if placeholder_id not in process.placeholders_map:
            raise ParrotOSUserError(
                ValueError(f"Unknown placeholder_id: {placeholder_id}")
            )

        placeholder = process.placeholders_map[placeholder_id]

        # NOTE(chaofan): The "start event" of the placeholder is set when the related process is executed.
        # It is to ensure the "check_process" is called after the process is executed.
        await placeholder.start_event.wait()

        placeholder.set(content)

        logger.debug(
            f"Placeholder set (id={placeholder_id}) from VM (pid={pid}). "
            f"Set content length: {len(content)} "
        )

    async def placeholder_fetch(self, pid: int, placeholder_id: int):
        """Fetch a placeholder content from OS to VM."""

        self._check_process(pid)

        process = self.processes[pid]
        if placeholder_id not in process.placeholders_map:
            raise ParrotOSUserError(
                ValueError(f"Unknown placeholder_id: {placeholder_id}")
            )
        placeholder = process.placeholders_map[placeholder_id]

        # NOTE(chaofan): The "start event" of the placeholder is set when the related process is executed.
        # It is to ensure the "check_process" is called after the process is executed.

        # with cprofile("wait_placeholder_start"):
        await placeholder.start_event.wait()

        # NOTE(chaofan): Recheck the process since it may become bad after starting.
        self._check_process(pid)

        logger.debug(f"Placeholder (id={placeholder_id}) fetching from VM (pid={pid})")

        # with cprofile("wait_placeholder_get"):
        content = await placeholder.get()

        logger.debug(f"Placeholder (id={placeholder_id}) fetched.")

        return content

    async def os_loop(self):
        """Start the OS loop."""

        while True:
            self._check_expired()
            self._sweep_dead_clients()

            threads = self.dispatcher.dispatch()

            for thread in threads:
                thread.process.execute_thread(thread)

            for process in self.processes.values():
                if process.live:
                    process.monitor_threads()

            await asyncio.sleep(OS_LOOP_INTERVAL)