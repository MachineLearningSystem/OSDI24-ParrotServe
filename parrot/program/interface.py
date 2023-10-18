import inspect
from typing import Optional, List

from parrot.protocol.sampling_config import SamplingConfig

from .function import SemanticFunction, ParamType, Parameter
from .transforms.prompt_formatter import standard_formatter, Sequential, FuncMutator


# Annotations of arguments when defining a parrot function.


class Input:
    """Annotate the input."""


class Output:
    """Annotate the output."""

    def __init__(self, *args, **kwargs):
        self.sampling_config = SamplingConfig(*args, **kwargs)


def function(
    models: List[str] = [],
    cache_prefix: bool = True,
    formatter: Optional[Sequential] = standard_formatter,
    conversation_template: Optional[FuncMutator] = None,
):
    """A decorator for users to define parrot functions."""

    def create_func(f):
        func_name = f.__name__
        doc_str = f.__doc__

        # Parse the function signature (parameters)
        func_sig = inspect.signature(f)
        func_params = []
        for param in func_sig.parameters.values():
            # assert param.annotation in (
            #     Input,
            #     Output,
            # ), "The arguments must be annotated by Input/Output"
            sampling_config: Optional[SamplingConfig] = None
            if param.annotation == Input:
                param_typ = ParamType.INPUT_LOC
            elif param.annotation == Output:
                # Default output loc
                param_typ = ParamType.OUTPUT_LOC
                sampling_config = SamplingConfig()
            elif param.annotation.__class__ == Output:
                # Output loc with sampling config
                param_typ = ParamType.OUTPUT_LOC
                sampling_config = param.annotation.sampling_config
            else:
                param_typ = ParamType.INPUT_PYOBJ
            func_params.append(
                Parameter(
                    name=param.name, typ=param_typ, sampling_config=sampling_config
                )
            )

        semantic_func = SemanticFunction(
            name=func_name,
            params=func_params,
            models=models,
            cache_prefix=cache_prefix,
            func_body_str=doc_str,
        )

        if formatter is not None:
            semantic_func = formatter.transform(semantic_func)
        if conversation_template is not None:
            semantic_func = conversation_template.transform(semantic_func)

        return semantic_func

    return create_func


# def shared_context(
#     engine_name: str,
#     parent_context: Optional[Context] = None,
# ) -> SharedContext:
#     """Interface to create a shared context."""

#     return SharedContext(engine_name, parent_context)
