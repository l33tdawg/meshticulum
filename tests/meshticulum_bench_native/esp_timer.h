#pragma once
#include "Arduino.h"
inline unsigned fake_clock_reads=0;
inline int64_t esp_timer_get_time() { fake_clock_reads++;return fake_time; }
