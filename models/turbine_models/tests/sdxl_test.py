# Copyright 2023 Nod Labs, Inc
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import logging
import pytest
import torch
from turbine_models.custom_models.sd_inference.utils import create_safe_name
from turbine_models.custom_models.sdxl_inference import (
    clip,
    clip_runner,
    unet,
    unet_runner,
    vae,
    vae_runner,
    sdxl_compiled_pipeline,
)
from turbine_models.utils.sdxl_benchmark import run_benchmark
import unittest
from tqdm.auto import tqdm
from PIL import Image
import os
import numpy as np
import time


torch.random.manual_seed(0)

arguments = {}


@pytest.fixture(scope="session")
def command_line_args(request):
    arguments["hf_auth_token"] = request.config.getoption("--hf_auth_token")
    arguments["hf_model_name"] = request.config.getoption("--hf_model_name")
    arguments["scheduler_id"] = request.config.getoption("--scheduler_id")
    arguments["prompt"] = request.config.getoption("--prompt")
    arguments["negative_prompt"] = request.config.getoption("--negative_prompt")
    arguments["num_inference_steps"] = int(
        request.config.getoption("--num_inference_steps")
    )
    arguments["guidance_scale"] = float(request.config.getoption("--guidance_scale"))
    arguments["seed"] = float(request.config.getoption("--seed"))
    arguments["vmfb_path"] = request.config.getoption("--vmfb_path")
    arguments["external_weight_path"] = request.config.getoption(
        "--external_weight_path"
    )
    arguments["external_weight_dir"] = request.config.getoption("--external_weight_dir")
    arguments["external_weight_file"] = request.config.getoption(
        "--external_weight_file"
    )
    arguments["pipeline_dir"] = request.config.getoption("--pipeline_dir")
    arguments["batch_size"] = int(request.config.getoption("--batch_size"))
    arguments["height"] = int(request.config.getoption("--height"))
    arguments["width"] = int(request.config.getoption("--width"))
    arguments["precision"] = request.config.getoption("--precision")
    arguments["max_length"] = int(request.config.getoption("--max_length"))
    arguments["run_vmfb"] = request.config.getoption("--run_vmfb")
    arguments["compile_to"] = request.config.getoption("--compile_to")
    arguments["external_weights"] = request.config.getoption("--external_weights")
    arguments["decomp_attn"] = request.config.getoption("--decomp_attn")
    arguments["attn_spec"] = request.config.getoption("--attn_spec")
    arguments["device"] = request.config.getoption("--device")
    arguments["rt_device"] = request.config.getoption("--rt_device")
    arguments["iree_target_triple"] = request.config.getoption("--iree_target_triple")
    arguments["ireec_flags"] = request.config.getoption("--ireec_flags")
    arguments["attn_flags"] = request.config.getoption("--attn_flags")
    arguments["in_channels"] = int(request.config.getoption("--in_channels"))
    arguments["benchmark"] = request.config.getoption("--benchmark")
    arguments["tracy_profile"] = request.config.getoption("--tracy_profile")
    arguments["compiled_pipeline"] = request.config.getoption("--compiled_pipeline")


@pytest.mark.usefixtures("command_line_args")
class StableDiffusionXLTest(unittest.TestCase):
    def setUp(self):
        self.safe_model_name = create_safe_name(arguments["hf_model_name"], "")
        self.unet_model = unet.UnetModel(
            # This is a public model, so no auth required
            arguments["hf_model_name"],
            precision=arguments["precision"],
        )
        self.vae_model = vae.VaeModel(
            # This is a public model, so no auth required
            arguments["hf_model_name"],
            custom_vae=(
                "madebyollin/sdxl-vae-fp16-fix"
                if arguments["precision"] == "fp16"
                else None
            ),
        )

    def test01_ExportClipModels(self):
        if arguments["device"] in ["vulkan", "cuda", "rocm"]:
            self.skipTest(
                "Compilation error on vulkan; Runtime error on rocm; To be tested on cuda."
            )
        clip.export_clip_model(
            # This is a public model, so no auth required
            hf_model_name=arguments["hf_model_name"],
            hf_auth_token=None,
            max_length=arguments["max_length"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_clip",
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            index=1,
            exit_on_vmfb=True,
        )
        clip.export_clip_model(
            hf_model_name=arguments["hf_model_name"],
            hf_auth_token=None,  # This is a public model, so no auth required
            max_length=arguments["max_length"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_clip",
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            index=2,
            exit_on_vmfb=True,
        )
        arguments["external_weight_path_1"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_clip_1."
            + arguments["external_weights"]
        )
        arguments["external_weight_path_2"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_clip_2."
            + arguments["external_weights"]
        )
        arguments["vmfb_path_1"] = (
            self.safe_model_name
            + "_"
            + str(arguments["max_length"])
            + "_"
            + arguments["precision"]
            + "_clip_1_"
            + arguments["device"]
            + ".vmfb"
        )
        arguments["vmfb_path_2"] = (
            self.safe_model_name
            + "_"
            + str(arguments["max_length"])
            + "_"
            + arguments["precision"]
            + "_clip_2_"
            + arguments["device"]
            + ".vmfb"
        )
        turbine_1 = clip_runner.run_clip(
            arguments["rt_device"],
            arguments["prompt"],
            arguments["vmfb_path_1"],
            arguments["hf_model_name"],
            arguments["hf_auth_token"],
            arguments["external_weight_path_1"],
            arguments["max_length"],
            index=1,
        )
        turbine_2 = clip_runner.run_clip(
            arguments["rt_device"],
            arguments["prompt"],
            arguments["vmfb_path_2"],
            arguments["hf_model_name"],
            arguments["hf_auth_token"],
            arguments["external_weight_path_2"],
            arguments["max_length"],
            index=2,
        )
        torch_output_1, torch_output_2 = clip_runner.run_torch_clip(
            arguments["hf_model_name"],
            arguments["hf_auth_token"],
            arguments["prompt"],
            arguments["max_length"],
        )
        if arguments["benchmark"] or arguments["tracy_profile"]:
            run_benchmark(
                "clip_1",
                arguments["vmfb_path_1"],
                arguments["external_weight_path_1"],
                arguments["rt_device"],
                max_length=arguments["max_length"],
                tracy_profile=arguments["tracy_profile"],
            )
            run_benchmark(
                "clip_2",
                arguments["vmfb_path_2"],
                arguments["external_weight_path_2"],
                arguments["rt_device"],
                max_length=arguments["max_length"],
                tracy_profile=arguments["tracy_profile"],
            )
        rtol = 4e-1
        atol = 4e-1
        np.testing.assert_allclose(torch_output_1, turbine_1[0], rtol, atol)
        np.testing.assert_allclose(torch_output_2, turbine_2[0], rtol, atol)

    def test02_ExportUnetModel(self):
        if arguments["device"] in ["vulkan", "cuda", "rocm"]:
            self.skipTest(
                "Unknown error on vulkan; Runtime error on rocm; To be tested on cuda."
            )
        unet.export_unet_model(
            unet_model=self.unet_model,
            # This is a public model, so no auth required
            hf_model_name=arguments["hf_model_name"],
            batch_size=arguments["batch_size"],
            height=arguments["height"],
            width=arguments["width"],
            precision=arguments["precision"],
            max_length=arguments["max_length"],
            hf_auth_token=None,
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_unet."
            + arguments["external_weights"],
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            decomp_attn=arguments["decomp_attn"],
        )
        arguments["external_weight_path"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_unet."
            + arguments["external_weights"]
        )
        arguments["vmfb_path"] = (
            self.safe_model_name
            + "_"
            + str(arguments["max_length"])
            + "_"
            + str(arguments["height"])
            + "x"
            + str(arguments["width"])
            + "_"
            + arguments["precision"]
            + "_unet_"
            + arguments["device"]
            + ".vmfb"
        )
        dtype = torch.float16 if arguments["precision"] == "fp16" else torch.float32
        sample = torch.rand(
            (
                arguments["batch_size"],
                arguments["in_channels"],
                arguments["height"] // 8,
                arguments["width"] // 8,
            ),
            dtype=dtype,
        )
        timestep = torch.zeros(1, dtype=torch.int64)
        prompt_embeds = torch.rand(
            (2 * arguments["batch_size"], arguments["max_length"], 2048),
            dtype=dtype,
        )
        text_embeds = torch.rand(2 * arguments["batch_size"], 1280, dtype=dtype)
        time_ids = torch.zeros(2 * arguments["batch_size"], 6, dtype=dtype)
        guidance_scale = torch.Tensor([arguments["guidance_scale"]]).to(dtype)

        turbine = unet_runner.run_unet(
            arguments["rt_device"],
            sample,
            timestep,
            prompt_embeds,
            text_embeds,
            time_ids,
            guidance_scale,
            arguments["vmfb_path"],
            arguments["hf_model_name"],
            arguments["hf_auth_token"],
            arguments["external_weight_path"],
        )
        torch_output = unet_runner.run_torch_unet(
            arguments["hf_model_name"],
            arguments["hf_auth_token"],
            sample.float(),
            timestep,
            prompt_embeds.float(),
            text_embeds.float(),
            time_ids.float(),
            guidance_scale.float(),
            precision=arguments["precision"],
        )
        if arguments["benchmark"] or arguments["tracy_profile"]:
            run_benchmark(
                "unet",
                arguments["vmfb_path"],
                arguments["external_weight_path"],
                arguments["rt_device"],
                max_length=arguments["max_length"],
                height=arguments["height"],
                width=arguments["width"],
                batch_size=arguments["batch_size"],
                in_channels=arguments["in_channels"],
                precision=arguments["precision"],
                tracy_profile=arguments["tracy_profile"],
            )
        rtol = 4e-2
        atol = 4e-2

        np.testing.assert_allclose(torch_output, turbine, rtol, atol)

    def test03_ExportVaeModelDecode(self):
        if arguments["device"] in ["vulkan", "cuda", "rocm"]:
            self.skipTest(
                "Compilation error on vulkan; Runtime error on rocm; To be tested on cuda."
            )
        vae.export_vae_model(
            vae_model=self.vae_model,
            # This is a public model, so no auth required
            hf_model_name=arguments["hf_model_name"],
            batch_size=arguments["batch_size"],
            height=arguments["height"],
            width=arguments["width"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_vae_decode."
            + arguments["external_weights"],
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            variant="decode",
            decomp_attn=arguments["decomp_attn"],
            exit_on_vmfb=True,
        )
        arguments["external_weight_path"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_vae_decode."
            + arguments["external_weights"]
        )
        arguments["vmfb_path"] = (
            self.safe_model_name
            + "_"
            + str(arguments["height"])
            + "x"
            + str(arguments["width"])
            + "_"
            + arguments["precision"]
            + "_vae_decode_"
            + arguments["device"]
            + ".vmfb"
        )
        example_input = torch.ones(
            arguments["batch_size"],
            4,
            arguments["height"] // 8,
            arguments["width"] // 8,
            dtype=torch.float32,
        )
        example_input_torch = example_input
        if arguments["precision"] == "fp16":
            example_input = example_input.half()
        turbine = vae_runner.run_vae(
            arguments["rt_device"],
            example_input,
            arguments["vmfb_path"],
            arguments["hf_model_name"],
            arguments["external_weight_path"],
        )
        torch_output = vae_runner.run_torch_vae(
            arguments["hf_model_name"],
            (
                "madebyollin/sdxl-vae-fp16-fix"
                if arguments["precision"] == "fp16"
                else ""
            ),
            "decode",
            example_input_torch,
        )
        if arguments["benchmark"] or arguments["tracy_profile"]:
            run_benchmark(
                "vae_decode",
                arguments["vmfb_path"],
                arguments["external_weight_path"],
                arguments["rt_device"],
                height=arguments["height"],
                width=arguments["width"],
                precision=arguments["precision"],
                tracy_profile=arguments["tracy_profile"],
            )
        rtol = 4e-2
        atol = 4e-2

        np.testing.assert_allclose(torch_output, turbine, rtol, atol)

    def test04_ExportVaeModelEncode(self):
        if arguments["device"] in ["cpu", "vulkan", "cuda", "rocm"]:
            self.skipTest(
                "Compilation error on cpu, vulkan and rocm; To be tested on cuda."
            )
        vae.export_vae_model(
            vae_model=self.vae_model,
            # This is a public model, so no auth required
            hf_model_name=arguments["hf_model_name"],
            batch_size=arguments["batch_size"],
            height=arguments["height"],
            width=arguments["width"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_vae_encode."
            + arguments["external_weights"],
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            variant="encode",
            decomp_attn=arguments["decomp_attn"],
            exit_on_vmfb=True,
        )
        arguments["external_weight_path"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_vae_encode."
            + arguments["external_weights"]
        )
        arguments["vmfb_path"] = (
            self.safe_model_name
            + "_"
            + str(arguments["height"])
            + "x"
            + str(arguments["width"])
            + "_"
            + arguments["precision"]
            + "_vae_encode_"
            + arguments["device"]
            + ".vmfb"
        )
        example_input = torch.ones(
            arguments["batch_size"],
            3,
            arguments["height"],
            arguments["width"],
            dtype=torch.float32,
        )
        example_input_torch = example_input
        if arguments["precision"] == "fp16":
            example_input = example_input.half()
        turbine = vae_runner.run_vae(
            arguments["rt_device"],
            example_input,
            arguments["vmfb_path"],
            arguments["hf_model_name"],
            arguments["external_weight_path"],
        )
        torch_output = vae_runner.run_torch_vae(
            arguments["hf_model_name"],
            (
                "madebyollin/sdxl-vae-fp16-fix"
                if arguments["precision"] == "fp16"
                else ""
            ),
            "encode",
            example_input_torch,
        )
        if arguments["benchmark"] or arguments["tracy_profile"]:
            run_benchmark(
                "vae_encode",
                arguments["vmfb_path"],
                arguments["external_weight_path"],
                arguments["rt_device"],
                height=arguments["height"],
                width=arguments["width"],
                precision=arguments["precision"],
                tracy_profile=arguments["tracy_profile"],
            )
        rtol = 4e-2
        atol = 4e-2
        np.testing.assert_allclose(torch_output, turbine, rtol, atol)

    def test05_t2i_generate_images(self):
        if arguments["device"] in ["vulkan", "cuda", "rocm"]:
            self.skipTest("Have issues with submodels on these backends")
        mlirs = {
            "vae_decode": None,
            "prompt_encoder": None,
            "scheduled_unet": None,
            "pipeline": None,
            "full_pipeline": None,
        }
        vmfbs = {
            "vae_decode": None,
            "prompt_encoder": None,
            "scheduled_unet": None,
            "pipeline": None,
            "full_pipeline": None,
        }
        weights = {
            "vae_decode": None,
            "prompt_encoder": None,
            "scheduled_unet": None,
            "pipeline": None,
            "full_pipeline": None,
        }

        if not arguments["pipeline_dir"]:
            pipe_id_list = [
                "sdxl_1_0",
                str(arguments["height"]),
                str(arguments["width"]),
                str(arguments["max_length"]),
                arguments["precision"],
                arguments["device"],
            ]
            arguments["pipeline_dir"] = os.path.join(
                ".",
                "_".join(pipe_id_list),
            )
        ireec_flags = {
            "unet": arguments["ireec_flags"],
            "vae": arguments["ireec_flags"],
            "clip": arguments["ireec_flags"],
            "pipeline": arguments["ireec_flags"],
        }
        user_mlir_list = []
        for submodel_id, mlir_path in zip(mlirs.keys(), user_mlir_list):
            if submodel_id in mlir_path:
                mlirs[submodel_id] = mlir_path
        external_weights_dir = arguments["pipeline_dir"]
        sdxl_pipe = sdxl_compiled_pipeline.SharkSDXLPipeline(
            arguments["hf_model_name"],
            arguments["scheduler_id"],
            arguments["height"],
            arguments["width"],
            arguments["precision"],
            arguments["max_length"],
            arguments["batch_size"],
            arguments["num_inference_steps"],
            arguments["device"],
            arguments["iree_target_triple"],
            ireec_flags,
            arguments["attn_spec"],
            arguments["decomp_attn"],
            arguments["pipeline_dir"],
            external_weights_dir,
            arguments["external_weights"],
        )
        vmfbs, weights = sdxl_pipe.check_prepared(
            mlirs, vmfbs, weights, interactive=False
        )
        sdxl_pipe.load_pipeline(
            vmfbs, weights, arguments["rt_device"], arguments["compiled_pipeline"]
        )
        sdxl_pipe.generate_images(
            arguments["prompt"],
            arguments["negative_prompt"],
            1,
            arguments["guidance_scale"],
            arguments["seed"],
        )
        print("Image generation complete.")
        os.remove(os.path.join(arguments["pipeline_dir"], "prompt_encoder.vmfb"))
        os.remove(
            os.path.join(
                arguments["pipeline_dir"],
                arguments["scheduler_id"]
                + "_unet_"
                + str(arguments["num_inference_steps"])
                + ".vmfb",
            )
        )
        os.remove(os.path.join(arguments["pipeline_dir"], "vae_decode.vmfb"))
        os.remove(os.path.join(arguments["pipeline_dir"], "full_pipeline.vmfb"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
