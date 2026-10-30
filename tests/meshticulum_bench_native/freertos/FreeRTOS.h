#pragma once
// Source-only pinned API model; no actual task, interrupt or native permission.
#include "Arduino.h"
#define configUSE_TASK_NOTIFICATIONS 1
#define configTICK_RATE_HZ 1000
using TaskHandle_t=void*;
using BaseType_t=int;
using TickType_t=uint32_t;
using StackType_t=uint8_t;
using UBaseType_t=uint32_t;
constexpr BaseType_t pdFALSE=0,pdTRUE=1;
