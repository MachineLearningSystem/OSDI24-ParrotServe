# Parrot: Serving LLM-based Agents with Dependent Semantic Variables

This project is a research prototype for now. Being eargerly iterated.

## Install

**0. Environment Settings:**

- OS: Linux
- GPU: cc >= 7.0
- CUDA version: >= 12.1
- DL Framework: PyTorch >= 2.1.0 with CUDA 12.1.

```bash
pip install torch==2.1.0 --upgrade --index-url https://download.pytorch.org/whl/cu121
```


**1. Clone the Project:**

```bash
git clone --recursive https://github.com/SiriusNEO/LLMOS-Parrot.git
```

**2. Install dependencies:**

- Step 1: Install basic requirements.

```bash
pip install -r requirements.txt
```

- Step 2: Install necessary dependencies listed in `3rdparty` folder.

```bash
cd 3rdparty/vllm
pip install -e .
```

- Step 3 (Optional): Install Optional dependencies.

(Optional) FastChat and Langchain are used only in our benchmark.

```bash
cd 3rdparty/FastChat
pip install -e ".[model_worker,webui]"
```

```bash
cd 3rdparty/langchain/libs/langchain
pip install -e .
```

(Optional) MLC-LLM and OpenAI are a special type of engines.

If you used MLC-LLM engines, Follow the official guide of [MLC-LLM](https://github.com/mlc-ai/mlc-llm) to install it, including the pre-compiled library and weights. The recommended commit refers to `3rdparty` folder.

- Important Notes:

Triton 2.0.0 has some bugs in Kernel memory issues. So we enforce the version to be 2.1.0 here. You will see some dependencies warnings, but it will not affect the common usages. (The similar error also happens in [LightLLM](https://github.com/ModelTC/lightllm) kernels.)

```bash
pip install triton==2.1.0
```

**3. Install Parrot:**

```bash
python3 setup.py develop
```


## Run Parrot

**Run the Compose Script in a Single Machine**

We provide some one-click scripts to run Parrot in a single machine. You can check them in the `sample_configs/scripts` folder.

```bash
bash sample_configs/scripts/launch_single_vicuna_13b.sh
```

<!-- **Run Docker Compose in a Cluster**

TODO -->

**Start an OS Server**

You can separately start an OS server.

```bash
python3 -m parrot.os.http_server --config_path <config_path>
```

**Start an Engine Server**

You can separately start an engine server. If you choose to connect to the OS server, you need to start the OS server first and specify the OS server address in the config file.

```bash
python3 -m parrot.engine.http_server --config_path <config_path>
```