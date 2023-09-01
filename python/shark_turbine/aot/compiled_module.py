# Copyright 2023 Nod Labs, Inc
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

from typing import Callable, Dict, Optional, Union

import inspect
import logging
import re
import weakref

from iree.compiler.ir import (
    Context,
    Location,
    Module,
    Operation,
    StringAttr,
)

from . import builtins
from .builder import ModuleBuilder

logger = logging.getLogger("shark_turbine.aot")

__all__ = [
    "CompiledModule",
]

################################################################################
# Data structures
################################################################################


class PyOnlyDef:
    """Exportable that does not export but can be resolved in Python."""

    __slots__ = ["py_value"]

    def __init__(self, py_value):
        self.py_value = py_value

    def __str__(self):
        return str(self.py_value)

    def __repr__(self):
        return repr(self.py_value)

    def __call__(self, *args, **kwargs):
        return self.py_value(*args, **kwargs)


class ExportFunctionDef:
    __slots__ = [
        "callable",
        "export_name",
        "signature",
    ]

    def __init__(self, export_name: str, callable: Callable, *, signature):
        self.export_name = export_name
        self.callable = callable
        self.signature = signature

    def copy(self) -> "ExportFunctionDef":
        return ExportFunctionDef(
            self.export_name, self.callable, signature=self.signature
        )

    def __repr__(self):
        return f"<def {self.export_name}({self.signature})>"


Exportable = Union[ExportFunctionDef, PyOnlyDef]


class CompiledModuleClassInfo:
    __slots__ = [
        "all_exports",
        "ir_module_name",
    ]

    def __init__(self, *, ir_module_name: str):
        self.ir_module_name = ir_module_name
        self.all_exports: Dict[str, Exportable] = dict()

    def add_export(self, key: str, value: Exportable):
        if key in self.all_exports:
            raise TypeError(f"Cannot export attribute more than once: {key}")
        self.all_exports[key] = value

    def def_attribute(self, key, value):
        # Detect known decorators.
        if isinstance(value, PyOnlyDef):
            logging.debug("DEFINE PY_ONLY: %s = %r", key, value)
            self.add_export(key, value)
            return value

        # Infer if it is an exported function.
        if callable(value) and inspect.isfunction(value):
            return self.def_export_function(key, value)

        raise TypeError(
            f"cannot set arbitrary Python value '{key}' on "
            f"compiled module: {value!r}"
        )

    def def_export_function(self, name, f) -> ExportFunctionDef:
        logging.debug("DEFINE EXPORT: %s = %r", name, f)
        sig = inspect.signature(f)
        if len(sig.parameters) < 1:
            raise TypeError(
                f"export function '{name}' is expected to have a 'self' parameter"
            )

        # By default, we discover signature details from default values
        # on the function. But we should also source from an annotation.
        input_sig = []
        parameter_list = list(sig.parameters.values())
        # TODO: Reconstitute a pytree so as to handle kwargs?
        for param in parameter_list[1:]:
            if (
                param.kind != inspect.Parameter.POSITIONAL_ONLY
                and param.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD
            ):
                raise TypeError(
                    f"exported functions only support positional parameters"
                )
            param_desc = None
            if param.default and not param.empty:
                param_desc = param.default

            if param_desc is None:
                # TODO: Merge from a decorator?
                raise TypeError(
                    f"export function {name} missing required default value annotation "
                    f"for '{param.name}'"
                )

            # TODO: Convert to meta tensor or something.
            input_sig.append(param_desc)

        info = ExportFunctionDef(name, f, signature=input_sig)
        self.add_export(name, info)
        return info


class CompiledModuleInstanceInfo:
    """Info class for compiled module instances."""

    def __init__(
        self, class_info: CompiledModuleClassInfo, module_builder: ModuleBuilder
    ):
        self.class_info = class_info
        self.module_builder = module_builder


################################################################################
# Live reference accounting
################################################################################

_all_compiled_module_class_infos: Dict[
    "CompiledModuleMeta", CompiledModuleClassInfo
] = weakref.WeakKeyDictionary()

_all_compiled_module_instance_infos: Dict[
    "CompiledModule", CompiledModuleInstanceInfo
] = weakref.WeakKeyDictionary()


################################################################################
# CompiledModule and metaclass
################################################################################

# Gate that is set to True once metaclass setup is complete.
_metaclass_setup_complete = False


@property
def _blackhole_instance_attribute(self):
    # We're not here.
    raise AttributeError


_COMPILED_MODULE_API_ATTRIBUTES = [
    "export_global",
    "get_class_info",
    "get_info",
    "get_module_builder",
    "get_mlir_module",
    "jittable",
]


class CompiledModuleMeta(type):
    """Metaclass for all CompiledModule subclasses.

    Do not use directly.
    """

    # __new__ on a metaclass is called when a new subclass is constructed.
    # It is passed the dictionary of declared attributes and any keyword
    # arguments from the class declaration:
    #   class Foo(Bar, kwarg="you probably just learned this is possible"):
    def __new__(mcls, name: str, bases, dct, *, export_name: Optional[str] = None):
        if not _metaclass_setup_complete:
            return type.__new__(mcls, name, bases, dct)

        ir_module_name = _derive_ir_module_name(name, export_name)
        logger.debug("Create new CompiledModule: %s", ir_module_name)
        info = CompiledModuleClassInfo(ir_module_name=ir_module_name)

        # Process that attributes that were set as part of class definition.
        # Any attributes that we decide are part of the compiled module
        # are removed and appropriately transferred to the backing info
        # hierarchy.
        del_attr_keys = set()
        for key, value in dct.items():
            if key.startswith("__") and key.endswith("__"):
                continue
            del_attr_keys.add(key)
            info.def_attribute(key, value)
        for key in del_attr_keys:
            del dct[key]

        # The CompiledModule exports a number of its own API methods, which
        # we explicitly hide on subclasses and instances.
        for key in _COMPILED_MODULE_API_ATTRIBUTES:
            if key not in dct:
                dct[key] = _blackhole_instance_attribute

        # Finish construction.
        new_class = type.__new__(mcls, name, bases, dct)
        _all_compiled_module_class_infos[new_class] = info
        return new_class

    # Gets unresolved attributes on classes of this meta-class.
    def __getattr__(cls, key):
        # CompiledModule does not expose anything else.
        if cls is CompiledModule:
            raise AttributeError(f"CompiledModule.{key}")
        info = CompiledModule.get_class_info(cls)
        try:
            return info.all_exports[key]
        except KeyError:
            raise AttributeError


class CompiledModule(metaclass=CompiledModuleMeta):
    """Base class for all staged modules."""

    @staticmethod
    def get_class_info(cls: CompiledModuleMeta) -> CompiledModuleClassInfo:
        return _all_compiled_module_class_infos[cls]

    @staticmethod
    def get_info(inst: "CompiledModule") -> CompiledModuleInstanceInfo:
        return _all_compiled_module_instance_infos[inst]

    @staticmethod
    def get_module_builder(inst: "CompiledModule") -> Operation:
        if not isinstance(inst, CompiledModule):
            raise ValueError(
                f"Expected a CompiledModule instance but got: {inst.__class__}"
            )
        info = CompiledModule.get_info(inst)
        return info.module_builder

    @staticmethod
    def get_mlir_module(inst: "CompiledModule") -> Operation:
        return CompiledModule.get_module_builder(inst).module_op

    @staticmethod
    def jittable(wrapped_f, *, decomposition_table=None, constraints=None):
        """Decorator which exports a PyTorch function into the module."""
        return PyOnlyDef(
            builtins.jittable(
                wrapped_f,
                decomposition_table=decomposition_table,
                constraints=constraints,
            )
        )

    def __new__(
        cls, *, context: Optional[Context] = None, module_op: Optional[Operation] = None
    ):
        self = super().__new__(cls)
        class_info = CompiledModule.get_class_info(cls)
        if context and module_op:
            raise ValueError("Only one of context= or module_op= can be specified")
        if not context and not module_op:
            try:
                context = Context.current
            except ValueError:
                raise ValueError(
                    "Neither an implicit context context handler not "
                    "context= or module= arguments specified"
                )
        if context:
            loc = Location.unknown(context=context)
            module = Module.create(loc)
            module_op = module.operation
            module_op.attributes["sym_name"] = StringAttr.get(
                class_info.ir_module_name, context=context
            )
        module_builder = ModuleBuilder(module_op)
        info = CompiledModuleInstanceInfo(class_info, module_builder=module_builder)
        _all_compiled_module_instance_infos[self] = info

        return self


_metaclass_setup_complete = True

################################################################################
# Utilities
################################################################################


def _derive_ir_module_name(class_name: str, explicit_name: Optional[str]):
    """Returns an appropriate module export name given a class name and override.

    If an explicit_name is given, that is used as is. Otherwise, the class name
    is mangled by:
      * Removing and "Module" suffix.
      * Converting camel case to snake case.
    """
    if explicit_name:
        return explicit_name
    return _to_snake_case(_strip_suffix(class_name, "Module"))


def _to_snake_case(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def _strip_suffix(s: str, optional_suffix: str) -> str:
    if s.endswith(optional_suffix):
        return s[0 : len(s) - len(optional_suffix)]
    else:
        return s
