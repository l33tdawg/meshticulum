#pragma once
// Source-only pinned API model; no actual task, interrupt or native permission.
#include "Arduino.h"
#define configUSE_TASK_NOTIFICATIONS 1
#define configTICK_RATE_HZ 1000
using TaskHandle_t=void*;
using BaseType_t=int;
using TickType_t=uint32_t;
constexpr BaseType_t pdFALSE=0,pdTRUE=1;
