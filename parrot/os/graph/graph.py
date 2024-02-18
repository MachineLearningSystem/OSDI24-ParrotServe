# Copyright (c) 2023 by Microsoft Corporation.
# Licensed under the MIT license.


from typing import List, Optional, Dict

from parrot.protocol.sampling_config import SamplingConfig
from parrot.exceptions import parrot_assert

from .request import RequestPlaceholder
from .semantic_variable import SemanticVariable, SemanticVariableNamespace


# ---------- Graph ----------


class BaseNode:
    """Represent a computational node in the graph."""

    def __init__(self):
        # Basic Info
        self.sv: Optional[SemanticVariable] = None
        self.gen_task: Optional["GenTask"] = None

        # Graph
        self.id_in_graph: Optional[int] = None

        # Edge type A: Fill -> Fill -> Fill -> Gen -> Fill -> Fill -> Gen -> ...
        self.edge_a_prev_node: Optional["BaseNode"] = None
        self.edge_a_next_node: Optional["BaseNode"] = None

        # Flags
        # TODO: Remove these flags.
        # self.is_scheduled = (
        #     False  # Whether the GenTask of this node is scheduled to an engine.
        # )
        # self.is_finished = False  # Whether the GenTask of this node is finished.

    @property
    def is_inserted(self) -> bool:
        """Whether the node is inserted into the graph.
        If the node is inserted, it should have a SV attached.
        """

        return self.id_in_graph is not None and self.sv is not None

    @property
    def edge_b_prev_node(self) -> Optional["BaseNode"]:
        """Edge type B: prev node. 0 or 1."""

        parrot_assert(self.is_inserted, "Should be inserted before get DAG info.")
        return self.sv.producer

    @property
    def edge_b_next_nodes(self) -> List["BaseNode"]:
        """Edge type B: next node. Only Gen node has multiple next nodes."""

        parrot_assert(self.is_inserted, "Should be inserted before get DAG info.")
        if not isinstance(self, PlaceholderGen):
            return []
        return self.sv.consumers

    @property
    def in_degree(self) -> int:
        """In-degree of the node in the graph. It's the number of nodes pointing to this node.

        Possible values: 0, 1, 2.
        """

        return int(self.edge_a_prev_node is not None) + int(
            self.edge_b_prev_node is not None
        )

    @property
    def sv_name(self) -> str:
        if not self.is_inserted:
            return "(not inserted)"
        return self.sv.name

    @property
    def sv_id(self) -> str:
        if not self.is_inserted:
            return "(not inserted)"
        return self.sv.sv_id

    def has_placeholder(self) -> bool:
        return False

    async def get(self) -> str:
        """Get the content of the node."""

        return await self.sv.get()

    def _get_display_elements(self) -> Dict:
        raise NotImplementedError

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            + ", ".join([f"{k}={v}" for k, v in self._get_display_elements().items()])
            + ")"
        )

    def pretty_print(self) -> str:
        """Pretty print the node."""

        ret = self.__class__.__name__ + ":\n"
        for k, v in self._get_display_elements().items():
            ret += f"\t{k}: {v}\n"
        return ret

    def _short_repr_add_graph_id(self, repr: str) -> str:
        if self.is_inserted:
            return f"{self.id_in_graph}: " + repr
        return repr

    def short_repr(self) -> str:
        """Short representation of the node."""

        return self._short_repr_add_graph_id("BaseNode")


class ConstantFill(BaseNode):
    """Represent a fill node (constant) in the graph."""

    def __init__(self, constant_text: str):
        super().__init__()
        self.constant_text = constant_text

    def _get_display_elements(self) -> Dict:
        return {
            "sv_name": self.sv_name,
            "sv_id": self.sv_id,
            "constant_text": self.constant_text,
        }

    def short_repr(self) -> str:
        length_threshold = 7
        short_text = (
            self.constant_text[:length_threshold] + "..."
            if len(self.constant_text) > length_threshold
            else self.constant_text
        )
        return self._short_repr_add_graph_id("ConstantFill(" + short_text + ")")


class PlaceholderFill(BaseNode):
    """Represent a fill node (placeholder) in the graph."""

    def __init__(self, placeholder: RequestPlaceholder):
        super().__init__()
        self.placeholder = placeholder

    def _get_display_elements(self) -> Dict:
        return {
            "sv_name": self.sv_name,
            "sv_id": self.sv_id,
            "placeholder_name": self.placeholder.name,
        }

    def has_placeholder(self) -> bool:
        return True

    def short_repr(self) -> str:
        return self._short_repr_add_graph_id(
            f"PlaceholderFill({self.placeholder.name})"
        )


class PlaceholderGen(BaseNode):
    """Represent a gen node (placeholder, actually it must be) in the graph."""

    def __init__(self, placeholder: RequestPlaceholder):
        super().__init__()
        self.placeholder = placeholder

    @property
    def sampling_config(self) -> SamplingConfig:
        return self.placeholder.sampling_config

    def _get_display_elements(self) -> Dict:
        return {
            "sv_name": self.sv_name,
            "sv_id": self.sv_id,
            "placeholder_name": self.placeholder.name,
            "sampling_config": self.sampling_config,
        }

    def has_placeholder(self) -> bool:
        return True

    def short_repr(self) -> str:
        return self._short_repr_add_graph_id(f"PlaceholderGen({self.placeholder.name})")


class StaticGraph:
    """Computational graph of LLM requests linked by Semantic Variables."""

    def __init__(self):
        self.nodes: List[BaseNode] = []

        # ---------- Semantic Vars ----------
        self.vars: Dict[str, SemanticVariable] = {}  # id -> sv
        self.sv_namespace = SemanticVariableNamespace()

    def _get_new_var(
        self, sv_name: str, producer: Optional[BaseNode] = None
    ) -> SemanticVariable:
        """Get a new Semantic Variable."""

        sv_id = self.sv_namespace.get_new_id()
        sv = SemanticVariable(name=sv_name, sv_id=sv_id, producer=producer)
        self.vars[sv_id] = sv
        return sv

    def insert_node(self, node: BaseNode):
        """Insert a node into the graph. The node must be created
        without a SV and graph informaton.
        """

        self.nodes.append(node)
        node.id_in_graph = len(self.nodes) - 1

        if isinstance(node, ConstantFill):
            # Constant node has no topology in the graph.
            sv = self._get_new_var(sv_name="constant")
            sv.set(node.constant_text)
            sv.consumers.append(node)
            node.sv = sv
        elif isinstance(node, PlaceholderFill):
            placeholder = node.placeholder

            # For PlaceholderFill, the SV may be reused if it is already created.
            if placeholder.const_value is not None:
                sv = self._get_new_var(sv_name=placeholder.name)
                sv.set(placeholder.const_value)
            elif placeholder.var_id is not None:
                sv = self.vars[placeholder.var_id]
            else:
                sv = self._get_new_var(sv_name=placeholder.name)
            sv.consumers.append(node)
            node.sv = sv

            # Link with producer of this SV: This is automatically done by the SV.
            # ...
        elif isinstance(node, PlaceholderGen):
            placeholder = node.placeholder

            # For PlaceholderGen, the SV must be created.
            # And we don't link with other SVs, because it will be linked by the subsequent Fill nodes.
            sv = self._get_new_var(sv_name=placeholder.name, producer=node)
            node.sv = sv

    def get_ready_task(self) -> Optional["GenTask"]:
        """Get a ready GenTask from the graph. Return None if no ready task is found."""

        for node in self.nodes:
            if isinstance(node, PlaceholderGen):
                if node.gen_task.is_ready():
                    return node.gen_task

        parrot_assert(len(self.nodes) == 0, "Dead lock! No ready task found.")

        return None

    def remove_task(self, task: "GenTask"):
        """Remove a task from the graph. This is called when the task is finished."""

        pass
