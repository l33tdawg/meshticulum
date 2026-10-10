#pragma once
#include <cstdint>
inline bool preference_ok=true;
inline uint64_t boot_count=17;
struct Preferences {
    bool begin(const char*,bool) { return preference_ok; }
    uint64_t getULong64(const char*,uint64_t) { return boot_count; }
    size_t putULong64(const char*,uint64_t n) { boot_count=n; return 8; }
    void end() {}
};
