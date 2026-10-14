#pragma once
#include "FreeRTOS.h"
inline TaskHandle_t fake_loop_task=reinterpret_cast<TaskHandle_t>(1);
inline uint32_t fake_notifications=0,fake_notify_gives=0,fake_notify_waits=0,fake_notify_yields=0;
inline TaskHandle_t xTaskGetCurrentTaskHandle() { return fake_loop_task; }
inline void vTaskNotifyGiveFromISR(TaskHandle_t task,BaseType_t* woke) {
    assert(task==fake_loop_task && task!=nullptr);fake_notify_gives++;fake_notifications++;*woke=pdTRUE;
}
inline void portYIELD_FROM_ISR() { fake_notify_yields++; }
inline uint32_t ulTaskNotifyTake(BaseType_t clear,TickType_t ticks) {
    assert(clear==pdTRUE && ticks==1);fake_notify_waits++;
    const uint32_t pending=fake_notifications;fake_notifications=0;
    if(!pending) delay(ticks); // Same original one-tick clock progression.
    return pending;
}
