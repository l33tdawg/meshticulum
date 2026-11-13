#pragma once
// One persistent task-dispatched timer. A notification is never RF authority.
// The original loop must still service RX and re-check every burst condition.
#include <stdint.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace mtbench { namespace experimental {
class BurstWakeHint {
    esp_timer_handle_t timer_=nullptr;
    uint64_t target_=0;
    bool attempted_=false, disabled_=false;
    static void callback(void* task) {
        // Never dereference queue/owner objects or perform SPI from this task.
        if(task) xTaskNotifyGive(static_cast<TaskHandle_t>(task));
    }
public:
    // Call once, from the radio loop task after its lifetime-stable handle exists.
    void initialize(TaskHandle_t task) {
        if(attempted_) return;
        attempted_=true;
        if(!task) { disabled_=true;return; }
        esp_timer_create_args_t args{};
        args.callback=callback;args.arg=task;args.dispatch_method=ESP_TIMER_TASK;
        args.name="mtbench-burst-wake";
        if(esp_timer_create(&args,&timer_)!=ESP_OK) { timer_=nullptr;disabled_=true; }
    }
    // `eligible` is the existing absolute guard expiry, never a renewed budget.
    // Zero cancels the hint. The timer is intentionally never deleted: a queued
    // callback can then outlive a cancellation without accessing freed state.
    void update(uint64_t now,uint64_t eligible) {
        if(disabled_ || !timer_) return;
        const uint64_t next=eligible>now ? eligible : 0;
        if(next==target_) return;
        if(target_) {
            const esp_err_t stopped=esp_timer_stop(timer_);
            if(stopped!=ESP_OK && stopped!=ESP_ERR_INVALID_STATE) {
                disabled_=true;target_=0;return;
            }
        }
        target_=0;
        if(next) {
            // Delayed scheduling can only wake late; service checks never move.
            if(esp_timer_start_once(timer_,next-now)!=ESP_OK) { disabled_=true;return; }
            target_=next;
        }
    }
};
}}
