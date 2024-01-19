import logging
import unittest

import torch
import shark_turbine.kernel as tk

from shark_turbine.kernel.compiler import (
    builder,
    kernel_codegen,
    vector_codegen,
)
from shark_turbine.kernel._support import (
    indexing,
)

M = tk.lang.sym.M
K = tk.lang.sym.K


class Test(unittest.TestCase):
    # This test is using the compiler "the hard way" until we have all of the
    # API layering in place.
    def testIotaFx(self):
        @tk.gen.thread(M)
        def iota_kernel(out: tk.lang.OutputBuffer[M]):
            i = tk.lang.program_id(0)
            secret_value = ((i * (33 - i) + 4) % 8) // 2
            out[i] = secret_value

        trace = iota_kernel._trace
        print(trace.region_graph)
        mb = builder.ModuleBuilder()
        with indexing.IndexingContext() as idxc:
            idxc.bind_constant(M, 17)
            sig = kernel_codegen.KernelSignature()
            sig.add_from_graph_placeholders(trace.get_root_graph())
            sig.add_grid(iota_kernel.grid_type)
            print(sig)
            bound_sig, func_op = kernel_codegen.FunctionalKernelSignature.create(
                sig, mb
            )
            try:
                emitter = vector_codegen.ThreadEmitter(bound_sig, trace)
                emitter.emit()
                emitter.finish()
            finally:
                print(mb.module_op.get_asm())
            mb.module_op.verify()

    def testSoftmaxFx(self):
        @tk.gen.thread(M)
        def softmax_kernel(
            input: tk.lang.KernelBuffer[M, K], output: tk.lang.KernelBuffer[M, K]
        ):
            row_index = tk.lang.program_id(0)
            input_row = input[row_index, :]
            numerator = torch.exp(input_row - torch.max(input_row))
            output_row = numerator / torch.sum(numerator)
            output[row_index, :] = output_row

        trace = softmax_kernel._trace
        print(trace.region_graph)
        mb = builder.ModuleBuilder()
        with indexing.IndexingContext() as idxc:
            idxc.bind_constant(M, 128)
            idxc.bind_constant(K, 64)

            sig = kernel_codegen.KernelSignature()
            sig.add_from_graph_placeholders(trace.get_root_graph())
            sig.add_grid(softmax_kernel.grid_type)
            print(sig)
            bound_sig, func_op = kernel_codegen.FunctionalKernelSignature.create(
                sig, mb
            )
            emitter = vector_codegen.ThreadEmitter(bound_sig, trace)
            try:
                emitter.emit()
            finally:
                emitter.finish()
                print(mb.module_op.get_asm())
            mb.module_op.verify()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
