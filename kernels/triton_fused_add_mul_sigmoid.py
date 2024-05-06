import torch
import triton
import triton.language as tl


@triton.jit
def fused_add_mul_sigmoid_kernel(x_ptr, bias_ptr, in_ptr, num_weights, xnumel, multiplier,
                               BLOCK_SIZE: tl.constexpr):
    xoffset = tl.program_id(0) * BLOCK_SIZE
    index = xoffset + tl.arange(0, BLOCK_SIZE)[:]
    mask = index < xnumel
    bias_index = index % num_weights
    tmp0 = tl.load(x_ptr + index, mask)
    tmp1 = tl.load(bias_ptr + bias_index, mask, eviction_policy='evict_last')
    tmp3 = tl.load(in_ptr + index, mask)
    sigmoid_input = multiplier * tmp3 + tmp0 + tmp1
    # ma_result = tl.sigmoid(sigmoid_input)
    # ma_result = 1.0 / (1.0 + tl.exp(-sigmoid_input))
    ma_result = 1.0 / (1.0 + tl.abs(sigmoid_input))
    tl.store(x_ptr + index, ma_result, mask)

def fused_add_mul_sigmoid_torch(in_out_tensor: torch.Tensor, bias: torch.Tensor, in_tensor: torch.Tensor) -> torch.Tensor:
    # print("calling fused_add_mul_relu_torch")
    grid = lambda meta: (triton.cdiv(in_out_tensor.numel(), meta['BLOCK_SIZE']),)
    BLOCK_SIZE = min(2048, in_out_tensor.numel())
    fused_add_mul_sigmoid_kernel[grid](in_out_tensor, bias, in_tensor,
                                       bias.numel(), in_out_tensor.numel(), multiplier=0.5,
                                       BLOCK_SIZE=BLOCK_SIZE)
    return in_out_tensor

def add_mul_sigmoid_torch(in_out_tensor: torch.Tensor, bias: torch.Tensor, in_tensor: torch.Tensor) -> torch.Tensor:
    return torch.sigmoid(in_out_tensor + 0.5 * in_tensor + bias)

def get_inputs(batch_size: int = 8, weight_size: int = 8, add_manual_size: bool = False):
    if add_manual_size:
        torch.manual_seed(0)
    dense_size = (batch_size, weight_size)
    in_out_tensor = torch.randn(dense_size, device='cuda', dtype=torch.float64)
    in_tensor = torch.randn(dense_size, device='cuda', dtype=torch.float64)
    bias = torch.randn((1, weight_size), device='cuda', dtype=torch.float64)
    return in_out_tensor, in_tensor, bias


add_mul_sigmoid_torch_scripted = torch.jit.script(add_mul_sigmoid_torch, example_inputs=[get_inputs()])

@triton.testing.perf_report(
    triton.testing.Benchmark(
        x_names=['batch_size', 'weight_size'],  # Argument names to use as an x-axis for the plot.
        x_vals=[(2 ** i, 2 ** j) for i, j in zip(range(2, 18, 2), range(2, 10, 1))],
        # Different possible values for `x_name`.
        x_log=True,  # x axis is logarithmic.
        line_arg='provider',  # Argument name whose value corresponds to a different line in the plot.
        line_vals=['triton', 'torch', 'torch_scripted'],  # Possible values for `line_arg`.
        line_names=['triton', 'torch', 'torch_scripted'],  # Label name for the lines.
        styles=[('blue', '-'), ('green', ':'), ("red", '-.')],  # Line styles.
        ylabel='GB/s',  # Label name for the y-axis.
        plot_name='performance',  # Name for the plot. Used also as a file name for saving the plot.
        args={},  # Values for function arguments not in `x_names` and `y_name`.
    ))
def benchmark(batch_size, weight_size, provider):
    in_out_tensor, in_tensor, bias = get_inputs(batch_size=batch_size, weight_size=weight_size, add_manual_size=True)
    quantiles = [0.5, 0.2, 0.8]
    if provider == "triton":
        ms, min_ms, max_ms = triton.testing.do_bench(lambda: fused_add_mul_sigmoid_torch(in_out_tensor, bias, in_tensor),
                                                     quantiles=quantiles)
    if provider == "torch":
        ms, min_ms, max_ms = triton.testing.do_bench(lambda: add_mul_sigmoid_torch(in_out_tensor, bias, in_tensor),
                                                     quantiles=quantiles)

    if provider == "torch_scripted":
        ms, min_ms, max_ms = triton.testing.do_bench(lambda: add_mul_sigmoid_torch_scripted(in_out_tensor, bias, in_tensor),
                                                     quantiles=quantiles)
    gbps = lambda ms: 12 * (batch_size * weight_size) / ms * 1e-6
    return gbps(ms), gbps(max_ms), gbps(min_ms)

if __name__ == "__main__":
    benchmark.run(print_data=True, show_plots=False)
