import pyopencl as cl
import numpy as np

# 查找可用的平台和设备
platforms = cl.get_platforms()
print("Available platforms:")
for i, platform in enumerate(platforms):
    print(f"\t{i} - {platform.name}")
    for j, device in enumerate(platform.devices):
        print(f"\t\t{j} - {device.name} with {device.global_mem_size // (1024 * 1024)}MB of global memory")

# 选择第一个设备
ctx = cl.Context(devices=[platforms[0].devices[0]])

# 创建命令队列
queue = cl.CommandQueue(ctx)

# 定义OpenCL内核代码
kernel_code = """
__kernel void vec_add(__global float *a, __global float *b, __global float *c) {
    int id = get_global_id(0);
    c[id] = a[id] + b[id];
}
"""

# 创建程序对象并编译内核
prg = cl.Program(ctx, kernel_code).build()

# 创建输入数据
a = np.array([1, 2, 3, 4, 5], dtype=np.float32)
b = np.array([6, 7, 8, 9, 10], dtype=np.float32)
c = np.empty_like(a)

# 将数据从主机复制到设备
a_g = cl.Buffer(ctx, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=a)
b_g = cl.Buffer(ctx, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=b)
c_g = cl.Buffer(ctx, cl.mem_flags.WRITE_ONLY, c.nbytes)

# 执行内核
prg.vec_add(queue, a.shape, None, a_g, b_g, c_g)

# 将结果从设备复制到主机
cl.enqueue_copy(queue, c, c_g)

print("Result:", c)

python src/main.py
