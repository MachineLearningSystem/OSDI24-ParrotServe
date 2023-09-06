from typing import List, Dict, Type
import regex as re
from dataclasses import dataclass

from .placeholder import Placeholder


@dataclass
class FunctionPiece:
    """A piece in the function body."""

    idx: int


@dataclass
class Constant(FunctionPiece):
    """Constant text."""

    text: str


@dataclass
class Prefix(Constant):
    """Prefix in a function."""


@dataclass
class Parameter:
    name: str
    is_output: bool


@dataclass
class ParameterLoc(FunctionPiece):
    """An input/output location in the function."""

    param: Parameter


def parse_func_body(
    body_str: str, args_map: Dict[str, Parameter]
) -> List[FunctionPiece]:
    PLACEHOLDER_REGEX = "{{[a-zA-Z_][a-zA-Z0-9_]+}}"
    pattern = re.compile(PLACEHOLDER_REGEX)
    iterator = pattern.finditer(body_str)
    last_pos = 0

    ret: List[FunctionPiece] = []

    def push_to_body(piece_cls: Type[FunctionPiece], **kwargs):
        idx = len(ret)
        ret.append(piece_cls(idx=idx, **kwargs))

    for match in iterator:
        # Constant
        chunk = body_str[last_pos : match.start()]
        if chunk != "":
            if last_pos == 0:
                push_to_body(Prefix, text=chunk)
            else:
                push_to_body(Constant, text=chunk)

        var_name = body_str[match.start() + 2 : match.end() - 2]
        assert var_name in args_map, f"Parse failed: {var_name} is not defined."
        var = args_map[var_name]
        assert not (
            var.is_output and isinstance(ret[-1], ParameterLoc)
        ), "Output loc can't be adjacent to another loc."
        push_to_body(ParameterLoc, var=var)

        last_pos = match.end()

    if last_pos < len(body_str):
        push_to_body(Constant, body_str[last_pos:])

    return ret


class ParrotFunction:
    """Parrot function is a simplified abstraction of the "general" semantic function,
    which is used as examples when we play in the Parrot project.

    An example:
        ```
        Tell me a joke about {{topic}}. The joke must contains the
        following keywords: {{keyword}}. The following is the joke: {{joke}}.
        And giving a short explanation to show that why it is funny. The following is the
        explanation for the joke above: {{explanation}}.
        ```
    """

    def __init__(self, name: str, func_body_str: str, func_args: list):
        """For semantic function, function body is just a prompt template.
        After parsed, it turns to be a list of function pieces.
        """

        self.name = name
        self.params: List[Parameter] = [
            Parameter(name=arg[0], is_output=arg[1]) for arg in func_args
        ]
        self.params_map = dict([(param.name, param) for param in self.params])
        self.body: List[FunctionPiece] = parse_func_body(func_body_str, self.args_map)

    def __call__(self, *args: List[Placeholder], **kwargs: Dict[str, Placeholder]):
        """Calling a parrot function will not execute it immediately.
        Instead, this will submit the call to the executor."""

        bindings: Dict[str, Placeholder] = {}
        for i, placeholder in enumerate(args):
            bindings[self.params_map[i].name] = placeholder

        for name, placeholder in kwargs.items():
            assert (
                name not in bindings
            ), f"Function {self.name} got multiple values for argument {name}"
            bindings[name] = placeholder


class Promise:
    """Promise is a function call including the functions and bindings (var name ->
    placeholder)."""

    def __init__(self, func: ParrotFunction, bindings: Dict[str, Placeholder]):
        self.func = func
        self.bindings = bindings
