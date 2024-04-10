from typing import Any, Callable, Type, Optional, Sequence, Union, List
from dataclasses import dataclass
import torch.fx as fx

from .._support.indexing import (
    IndexExpr,
    IndexingContext,
    IndexSymbol,
    SymIndex,
    index_expr,
)

from .._support.tracing import CapturedTrace
from .functional_ops import read, write, mma
from ..compiler.builder import (
    IRProxyValue,
    ScalarBuilder,
)

from ..compiler.base import (
    CodegenError,
    NDEBUG,
    ValidationError,
)

from ..compiler.ir import (
    AffineMap,
    Attribute,
    AffineExpr,
    AffineMapAttr,
    ArrayAttr,
    FunctionType,
    VectorType,
    DenseElementsAttr,
    F32Type,
    IndexType,
    FloatAttr,
    InsertionPoint,
    IrType,
    Location,
    MemRefType,
    ShapedType,
    Value,
    VectorType,
    arith_d,
    func_d,
    math_d,
    vector_d,
    scf_d,
)

from .. import lang as tkl

from ..compiler.kernel_codegen import (
    BoundKernelSignature,
)

from ..compiler.vector_codegen import (
    cast_py_literal,
    cast_kernel_buffer,
    cast_slice_spec,
    cast_vector,
    extract_slice_starts,
)
import operator as py_operator


@dataclass
class NodeAttrs:
    # By default, integers are assumed signed. We propagate unsigned as graph
    # node attrs.
    unsigned: bool = False

    @staticmethod
    def load(py_value) -> "NodeAttrs":
        if isinstance(py_value, fx.Node):
            return NodeAttrs(unsigned=bool(py_value.meta.get("unsigned")))
        return NodeAttrs()

    def store(self, node: fx.Node):
        node.meta["unsigned"] = self.unsigned


class WaveEmitter:
    """Emits a 'warp function' as a `func` with a signature derived from the gm."""

    OP_HANDLERS: dict[Any, Callable[["WaveEmitter", fx.Node], None]] = {}

    def __init__(self, root_sig: BoundKernelSignature, trace: CapturedTrace):
        self._node_values: dict[fx.Node, List[IRProxyValue]] = {}
        self._root_sig = root_sig
        self.trace = trace
        self.ip = InsertionPoint(root_sig.entry_block)

    def lookup_node_values(self, node: fx.Node) -> List[Value]:
        assert NDEBUG or isinstance(node, fx.Node)
        values = self._node_values.get(node)
        if values is None:
            values = [self._root_sig.resolve_by_reference(("node", node))]
            self._node_values[node] = values
        return values

    def bind_node_proxy(
        self, node: fx.Node, proxy: IRProxyValue, *, attrs: Optional[NodeAttrs] = None
    ):
        """Binds a node's result to a Python/IR proxy object."""
        assert NDEBUG or (isinstance(node, fx.Node) and isinstance(proxy, IRProxyValue))
        assert (
            node not in self._node_values
        ), f"Cannot rebind node {node}: already bound"
        if attrs is not None:
            attrs.store(node)
        self._node_values[node] = [proxy]

    def bind_node_proxies(
        self,
        node: fx.Node,
        proxies: list[IRProxyValue],
        *,
        attrs: Optional[NodeAttrs] = None,
    ):
        """Binds a node's result to a list of Python/IR proxy object."""
        assert NDEBUG or (
            all(isinstance(p, IRProxyValue) for p in proxies)
            and isinstance(node, fx.Node)
        )
        assert (
            node not in self._node_values
        ), f"Cannot rebind node {node}: already bound"
        if attrs is not None:
            attrs.store(node)
        self._node_values[node] = proxies

    def emit(self):
        with self.ip, Location.unknown():
            self.emit_graph(self.trace.get_root_graph())

    def emit_function_call_node(self, node: fx.Node):
        target_op = node.target
        try:
            handler = self.OP_HANDLERS[target_op]
        except KeyError:
            raise CodegenError(f"No handler registered for op {target_op}")
        handler(self, node)
        # dump

    def emit_graph(self, graph: fx.Graph):
        """Emits the given graph at the current insertion point."""
        for node in graph.nodes:
            if node.op == "call_function":
                self.emit_function_call_node(node)
            if node.op == "output":
                return node.args

    def emit_subgraph(self, subgraph: fx.Graph, implicit_capture: list[fx.Node]):
        # Map subgraph freevars -> implicit_capture
        freevars = self.trace.region_graph.inner_freevars[subgraph]
        assert len(freevars) == len(
            implicit_capture
        ), f"Expected {len(freevars)} implicit capture args, got {len(implicit_capture)}"
        for freevar, arg in zip(freevars, implicit_capture):
            self._node_values[freevar.node] = self.lookup_node_values(arg)

        # Emit subgraph
        return self.emit_graph(subgraph)

    def finish(self):
        with self.ip, Location.unknown():
            func_d.ReturnOp([])


def handle_op(op):
    def decorator(f: Callable[["WaveEmitter", fx.Node], None]):
        WaveEmitter.OP_HANDLERS[op] = f
        return None

    return decorator


###############################################################################
# Python/scalar ops
###############################################################################
@handle_op(py_operator.call)
def _(emitter: WaveEmitter, node: fx.Node):
    breakpoint()


###############################################################################
# Core data movement and indexing ops
###############################################################################


###############################################################################
# Memory Ops
###############################################################################
@handle_op(read)
def _(emitter: WaveEmitter, node: fx.Node):
    # This is similar to tkl.store with fixed start indices for now.
    try:
        memory, elements_per_thread = node.args
    except ValueError as e:
        raise ValidationError("Malformed arguments") from e

    vector_shape = cast_py_literal(emitter, (16, 16))
    # memory has no IR node yet.
    kb_src, kb_ir_type, kb_py_type = cast_kernel_buffer(emitter, memory)
    ref_shape = kb_py_type.symbolic_shape
    # slice_spec = cast_slice_spec(emitter, ref_shape, None)
    # start_indices = extract_slice_starts(emitter, ref_shape, slice_spec)
    start_indices = [
        arith_d.constant(IndexType.get(), 0),
        arith_d.constant(IndexType.get(), 0),
    ]
    element_type = kb_ir_type.element_type
    vector_type = VectorType.get(vector_shape, element_type)
    pad_attr = ScalarBuilder.zero_attr(element_type)
    pad_value = arith_d.constant(element_type, pad_attr)
    result = vector_d.transfer_read(
        vector_type,
        kb_src,
        start_indices,
        AffineMap.get_minor_identity(len(ref_shape), len(vector_shape)),
        pad_value,
    )
    emitter.bind_node_proxy(node, IRProxyValue(result))


@handle_op(write)
def _(emitter: WaveEmitter, node: fx.Node):
    try:
        register, memory, elements_per_thread = node.args
    except ValueError as e:
        raise ValidationError("Malformed arguments") from e

    kb_dest, kb_ir_type, kb_py_type = cast_kernel_buffer(emitter, memory)
    dest_rank = kb_ir_type.rank
    ref_shape = kb_py_type.symbolic_shape
    # slice_spec = cast_slice_spec(emitter, ref_shape, multi_index)
    # start_indices = extract_slice_starts(emitter, ref_shape, slice_spec)
    start_indices = [
        arith_d.constant(IndexType.get(), 0),
        arith_d.constant(IndexType.get(), 0),
    ]
    if dest_rank != len(start_indices):
        raise CodegenError(
            f"Mismatched slice assignment: Expected rank {dest_rank}, got {len(start_indices)}"
        )
    # TODO: This fails currently because the register is not properly resolved.
    #       It stems from the function call.
    insert_vector = cast_vector(emitter, register, element_type=kb_ir_type.element_type)
    insert_type = VectorType(insert_vector.type)
    insert_rank = insert_type.rank

    # Special case rank-0 broadcast.
    if insert_rank == 0:
        broadcast_type = VectorType.get(dest_rank * [1], kb_ir_type.element_type)
        insert_vector = vector_d.broadcast(broadcast_type, insert_vector)

    permutation_map = AffineMap.get_minor_identity(dest_rank, insert_rank)
    vector_d.transfer_write(
        None,
        insert_vector,
        kb_dest,
        start_indices,
        AffineMapAttr.get(permutation_map),
    )


###############################################################################
# Math Ops
###############################################################################
@handle_op(mma)
def _(emitter: WaveEmitter, node: fx.Node):
    # TODO: lhs, rhs, acc are actually registers, not vectors.
    #       Currently this is handled exactly like tkl.dot
    try:
        lhs, rhs, acc = node.args
        lhs = cast_vector(emitter, lhs)
        rhs = cast_vector(emitter, rhs)
        acc = cast_vector(emitter, acc)
    except ValueError as e:
        raise ValidationError("Malformed arguments") from e

    vector_type = VectorType(lhs.type)
    element_type = vector_type.element_type
    rank = vector_type.rank

    n, m, k = (
        AffineExpr.get_dim(0),
        AffineExpr.get_dim(1),
        AffineExpr.get_dim(2),
    )
    indexing_maps = [
        AffineMap.get(3, 0, [n, k]),
        AffineMap.get(3, 0, [k, m]),
        AffineMap.get(3, 0, [n, m]),
    ]
    indexing_maps_attr = [AffineMapAttr.get(map) for map in indexing_maps]
    # TODO: Bad hack, please fix.
    iterator_types = ArrayAttr.get(
        [
            Attribute.parse("#vector.iterator_type<parallel>"),
            Attribute.parse("#vector.iterator_type<parallel>"),
            Attribute.parse("#vector.iterator_type<reduction>"),
        ]
    )
    result = vector_d.ContractionOp(
        acc.type,
        lhs,
        rhs,
        acc,
        indexing_maps_attr,
        iterator_types,
    ).result
    emitter.bind_node_proxy(node, IRProxyValue(result))


###############################################################################
# Control Flow ops
###############################################################################

###############################################################################
# Shape Manipulation Ops
###############################################################################

###############################################################################
# Conversion utilities
###############################################################################

###############################################################################
# Slice and indexing
###############################################################################
