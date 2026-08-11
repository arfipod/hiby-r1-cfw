#ifndef R1_CFW_PROTOCOL_H
#define R1_CFW_PROTOCOL_H

#include <stdint.h>

enum r1_cfw_route {
    R1_CFW_ROUTE_BACK = 0,
    R1_CFW_ROUTE_WIFI = 21,
    R1_CFW_ROUTE_BLUETOOTH = 22,
};

enum r1_cfw_screen_id {
    R1_CFW_SCREEN_MAIN = 1,
    R1_CFW_SCREEN_LAUNCHER = 2,
    R1_CFW_SCREEN_INTERNAL = 3,
    R1_CFW_SCREEN_SD = 4,
    R1_CFW_SCREEN_MEMORY = 5,
    R1_CFW_SCREEN_SYSTEM = 6,
    R1_CFW_SCREEN_ABOUT = 7,
    R1_CFW_SCREEN_APPEARANCE = 8,
};

enum r1_cfw_action_id {
    R1_CFW_ACTION_NONE = 0,
    R1_CFW_ACTION_BACK = 1,
    R1_CFW_ACTION_SSH_TOGGLE = 2,
    R1_CFW_ACTION_WIFI_ROUTE = 3,
    R1_CFW_ACTION_BLUETOOTH_ROUTE = 4,
    R1_CFW_ACTION_OPEN_PAGE = 5,
    R1_CFW_ACTION_LAUNCHER_TOGGLE = 6,
    R1_CFW_ACTION_LAUNCHER_REJECTED = 7,
    R1_CFW_ACTION_DRAG_IGNORED = 8,
    R1_CFW_ACTION_THEME_SET = 9,
    R1_CFW_ACTION_THEME_REJECTED = 10,
};

/* Emulator-only state record written to R1_CFW_TEST_STATE_PATH. All fields
 * are native little-endian uint32_t on the target. The record is rewritten
 * after each render and is never created when the environment variable is
 * unset. */
#define R1_CFW_TEST_STATE_MAGIC 0x52314346U
struct r1_cfw_test_state {
    uint32_t magic;
    uint32_t version;
    uint32_t sequence;
    uint32_t screen_id;
    uint32_t launcher_mask;
    uint32_t last_action;
    uint32_t route;
};

#endif
