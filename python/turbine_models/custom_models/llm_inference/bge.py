# Copyright 2023 Nod Labs, Inc
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import os
import sys
import re

from iree import runtime as ireert
import iree.compiler as ireec
from iree.compiler.ir import Context
import numpy as np
from shark_turbine.aot import *
from turbine_models.custom_models.sd_inference import utils
from turbine_models.model_builder import HFTransformerBuilder
import torch
import torch._dynamo as dynamo

import argparse

parser = argparse.ArgumentParser()
parser.add_argument(
    "--hf_auth_token", type=str, help="The Hugging Face auth token, required"
)
parser.add_argument(
    "--hf_model_name",
    type=str,
    help="HF model name",
    default="BAAI/bge-base-en-v1.5",
)
parser.add_argument("--compile_to", type=str, help="torch, linalg, vmfb")
parser.add_argument("--external_weight_path", type=str, default="")
parser.add_argument(
    "--external_weights",
    type=str,
    default=None,
    help="saves ir/vmfb without global weights for size and readability, options [safetensors]",
)
parser.add_argument("--device", type=str, default="cpu", help="cpu, cuda, vulkan, rocm")
# TODO: Bring in detection for target triple
parser.add_argument(
    "--iree_target_triple",
    type=str,
    default="",
    help="Specify vulkan target triple or rocm/cuda target device.",
)
parser.add_argument("--vulkan_max_allocation", type=str, default="4294967296")


def export_model(
    hf_model_name,
    hf_auth_token=None,
    compile_to="torch",
    external_weights=None,
    external_weight_path=None,
    device=None,
    target_triple=None,
    max_alloc=None,
):
    model = HFTransformerBuilder(hf_id=hf_model_name, hf_auth_token=hf_auth_token)

    mapper = {}
    utils.save_external_weights(
        mapper, model, external_weights, external_weight_path
    )

    class BgeModel(CompiledModule):
        if external_weights:
            params = export_parameters(
                model.model,
                external=True,
                external_scope="",
                name_mapper=mapper.get,
            )
        else:
            params = export_parameters(model.model)

        def run_embedding(self, inp=AbstractTensor(1, None, dtype=torch.int64)):#, mask=AbstractTensor(1, None, dtype=torch.int64)):
            constraints = [inp.dynamic_dim(1) <= 512] #+ [mask.dynamic_dim(1) == inp.dynamic_dim(1)]
            return jittable(model.model.forward)(inp, constraints=constraints)#attention_mask=mask, constraints=constraints)

    import_to = "INPUT" if compile_to == "linalg" else "IMPORT"
    inst = BgeModel(context=Context(), import_to=import_to)

    module_str = str(CompiledModule.get_mlir_module(inst))
    safe_name = utils.create_safe_name(hf_model_name, "-clip")
    if compile_to != "vmfb":
        return module_str
    else:
        utils.compile_to_vmfb(module_str, device, target_triple, max_alloc, safe_name)


if __name__ == "__main__":
    args = parser.parse_args()
    mod_str = export_model(
        args.hf_model_name,
        args.hf_auth_token,
        args.compile_to,
        args.external_weights,
        args.external_weight_path,
        args.device,
        args.iree_target_triple,
        args.vulkan_max_allocation,
    )
    safe_name = args.hf_model_name.split("/")[-1].strip()
    safe_name = re.sub("-", "_", safe_name)
    with open(f"{safe_name}.mlir", "w+") as f:
        f.write(mod_str)
    print("Saved to", safe_name + ".mlir")
