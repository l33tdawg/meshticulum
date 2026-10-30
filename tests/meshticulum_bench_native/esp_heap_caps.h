#pragma once
#include "Arduino.h"
constexpr uint32_t MALLOC_CAP_INTERNAL=1u<<11, MALLOC_CAP_8BIT=1u<<2;
struct multi_heap_info_t {
    size_t total_free_bytes=123456, minimum_free_bytes=100000, largest_free_block=65536;
};
inline multi_heap_info_t fake_heap_info;
inline unsigned fake_heap_queries=0;
inline void heap_caps_get_info(multi_heap_info_t* info,uint32_t caps) {
    assert(caps==(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
    fake_heap_queries++;*info=fake_heap_info;
}
