def pytest_addoption(parser):
    parser.addoption("--hf_auth_token", action="store", default=None)
    parser.addoption(
        "--hf_model_name",
        action="store",
        default="stabilityai/stable-diffusion-xl-base-1.0",
    )
    parser.addoption(
        "--safe_model_name",
        action="store",
        default="stable_diffusion_xl_base_1_0",
    )
    parser.addoption("--batch_size", action="store", default=1)
    parser.addoption("--height", action="store", default=1024)
    parser.addoption("--width", action="store", default=1024)
    parser.addoption("--precision", action="store", default="fp16")
    parser.addoption("--max_length", action="store", default=64)
    parser.addoption("--guidance_scale", action="store", default=7.5)
    parser.addoption("--run_vmfb", action="store", default=True)
    parser.addoption("--compile_to", action="store", default=None)
    parser.addoption("--vmfb_path", action="store", default="")
    parser.addoption("--external_weights", action="store", default="safetensors")
    parser.addoption("--external_weight_path", action="store", default="")
    parser.addoption("--device", action="store", default="cpu")
    parser.addoption("--rt_device", action="store", default="local-task")
    parser.addoption("--iree_target_triple", action="store", default="x86_64-linux-gnu")
    parser.addoption("--vulkan_max_allocation", action="store", default="4294967296")
    parser.addoption(
        "--prompt",
        action="store",
        default="a photograph of an astronaut riding a horse",
    )
    parser.addoption(
        "--negative_prompt",
        action="store",
        default="blurry, unsaturated, watermark, noisy, grainy, out of focus",
    )
    parser.addoption("--in_channels", action="store", default=4)
    parser.addoption("--num_inference_steps", action="store", default=35)
    parser.addoption("--benchmark", action="store", default=False)
    parser.addoption("--decomp_attn", action="store", default=False)
    parser.addoption("--tracy_profile", action="store", default=False)
