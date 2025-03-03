#pragma once

#include <c10/core/Device.h>
#include <c10/xpu/XPUDeviceProp.h>
#include <c10/xpu/XPUMacros.h>

// The naming convention used here matches the naming convention of torch.xpu

namespace c10::xpu {

// Log a warning only once if no devices are detected.
C10_XPU_API DeviceIndex device_count();

// Throws an error if no devices are detected.
C10_XPU_API DeviceIndex device_count_ensure_non_zero();

C10_XPU_API DeviceIndex current_device();

C10_XPU_API void set_device(DeviceIndex device);

C10_XPU_API c10::DeviceIndex exchange_device(c10::DeviceIndex device);

C10_XPU_API c10::DeviceIndex maybe_exchange_device(c10::DeviceIndex to_device);

C10_XPU_API sycl::device& get_raw_device(int device);

C10_XPU_API sycl::context& get_device_context();

C10_XPU_API void get_device_properties(DeviceProp* device_prop, int device);

C10_XPU_API int get_device_idx_from_pointer(void* ptr);

} // namespace c10::xpu
