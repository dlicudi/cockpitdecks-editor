"""Button templates for the Gallery action bar.

Data structure rules:
- activation uses nested dict: {"type": "...", "commands": {"press": "..."}}
- representation uses nested dict: {"type": "..."}
- Nested-block representations (annunciator, switch, circular-switch, gauge, etc.)
  store their visual parameters under representation[type]: {"type": "...", "switch": {...}}
- Root-level fields (label, text, formula, icon) sit directly in representation.
"""

TEMPLATES = {
    "Starters": {
        "Momentary Button": {
            "index": 0,
            "name": "my_button",
            "activation": {
                "type": "push",
                "commands": {"press": "sim/none/none"},
            },
            "representation": {
                "type": "icon-color",
                "label": "PUSH",
                "label-size": 14,
                "color": "steelblue",
            },
        },
        "Toggle On / Off": {
            "index": 0,
            "name": "my_toggle",
            "activation": {
                "type": "encoder-toggle",
                "commands": {
                    "toggle-on": "sim/none/none",
                    "toggle-off": "sim/none/none",
                },
            },
            "representation": {
                "type": "annunciator",
                "label": "TOGGLE",
                "label-size": 12,
                "annunciator": {
                    "model": "B",
                    "size": "medium",
                    "parts": [
                        {"color": "lime", "led": "bars", "formula": "0"},
                        {"text": "ON", "text-size": 36, "formula": "1"},
                    ],
                },
            },
        },
        "Page Navigation": {
            "index": 0,
            "name": "nav_home",
            "activation": {"type": "page", "page": "index"},
            "representation": {
                "type": "icon-color",
                "label": "HOME",
                "label-color": "Gold",
                "label-size": 12,
                "color": "midnightblue",
            },
        },
        "Status Display": {
            "index": 0,
            "name": "status_tile",
            "activation": {"type": "none"},
            "representation": {
                "type": "text",
                "label": "AIRSPEED",
                "label-size": 11,
                "text": "${sim/cockpit2/gauges/indicators/airspeed_kts_pilot}",
                "text-size": 28,
                "text-color": "white",
                "text-format": "{:.0f}",
            },
        },
    },
    "Switches": {
        "Round Toggle (2-pos)": {
            "index": 0,
            "name": "sw_round",
            "activation": {
                "type": "encoder-toggle",
                "commands": {
                    "toggle-on": "sim/lights/landing_lights_on",
                    "toggle-off": "sim/lights/landing_lights_off",
                },
            },
            "representation": {
                "type": "switch",
                "label": "LANDING",
                "label-size": 12,
                "switch": {
                    "switch-style": "round",
                    "handle-fill-color": "deepskyblue",
                    "tick-labels": [{"-label": "OFF"}, {"-label": "ON"}],
                },
            },
        },
        "Heavy Bat Switch (rect)": {
            "index": 0,
            "name": "sw_battery",
            "activation": {
                "type": "encoder-toggle",
                "commands": {
                    "toggle-on": "sim/electrical/battery_1",
                    "toggle-off": "sim/electrical/battery_1",
                },
            },
            "representation": {
                "type": "switch",
                "label": "BAT 1",
                "label-size": 12,
                "switch": {
                    "switch-style": "rect",
                    "handle-fill-color": "firebrick",
                    "tick-labels": [{"-label": "OFF"}, {"-label": "ON"}],
                },
            },
        },
        "3-Dot Switch": {
            "index": 0,
            "name": "sw_3dot",
            "activation": {
                "type": "encoder-toggle",
                "commands": {
                    "toggle-on": "sim/none/none",
                    "toggle-off": "sim/none/none",
                },
            },
            "representation": {
                "type": "switch",
                "label": "SWITCH",
                "label-size": 12,
                "switch": {
                    "switch-style": "3dot",
                    "handle-fill-color": "silver",
                    "tick-labels": [{"-label": "OFF"}, {"-label": "ON"}],
                },
            },
        },
        "Magnetos (circular, 5-pos)": {
            "index": 0,
            "name": "sw_magnetos",
            "activation": {
                "type": "encoder-toggle",
                "commands": {
                    "toggle-on": "sim/magnetos/magnetos_on",
                    "toggle-off": "sim/magnetos/magnetos_off",
                },
            },
            "representation": {
                "type": "circular-switch",
                "label": "MAGNETOS",
                "label-size": 11,
                "circular-switch": {
                    "tick-labels": [
                        {"-label": "OFF"},
                        {"-label": "L"},
                        {"-label": "R"},
                        {"-label": "BOTH"},
                        {"-label": "START"},
                    ],
                    "tick-from": -120,
                    "tick-to": 120,
                    "needle-color": "white",
                },
            },
        },
    },
    "Annunciators": {
        "Single State (green)": {
            "index": 0,
            "name": "ann_green",
            "activation": {"type": "none"},
            "representation": {
                "type": "annunciator",
                "label": "FUEL",
                "label-size": 12,
                "annunciator": {
                    "model": "A",
                    "parts": [
                        {"color": "lime", "text": "NORM", "text-size": 28, "formula": "1"},
                    ],
                },
            },
        },
        "Dual State (gear)": {
            "index": 0,
            "name": "ann_gear",
            "activation": {"type": "none"},
            "representation": {
                "type": "annunciator",
                "label": "GEAR",
                "label-size": 12,
                "annunciator": {
                    "model": "B",
                    "parts": [
                        {"color": "lime", "text": "SAFE", "text-size": 22, "formula": "1"},
                        {"color": "red", "text": "UNSAFE", "text-size": 18, "formula": "0"},
                    ],
                },
            },
        },
        "Push to test (amber blink)": {
            "index": 0,
            "name": "ann_caution",
            "activation": {
                "type": "push",
                "commands": {"press": "sim/annun/test_all_annunciators"},
            },
            "representation": {
                "type": "annunciator-animate",
                "label": "CAUTION",
                "label-size": 11,
                "annunciator": {
                    "model": "A",
                    "parts": [
                        {"color": "amber", "text": "MASTER\nCAUTION", "text-size": 20, "formula": "1"},
                    ],
                },
            },
        },
        "Fire handle (pull)": {
            "index": 0,
            "name": "ann_fire",
            "activation": {
                "type": "push",
                "commands": {"press": "sim/fire/fire_handle_pull_1"},
            },
            "representation": {
                "type": "annunciator",
                "label": "ENG 1",
                "label-size": 11,
                "annunciator": {
                    "model": "C",
                    "parts": [
                        {"color": "orangered", "text": "PULL", "text-size": 38, "formula": "1"},
                    ],
                },
            },
        },
    },
    "Sliders": {
        "Throttle Slider (web deck)": {
            "index": 0,
            "name": "throttle_slider",
            "span": [1, 3],
            "activation": {
                "type": "slider",
                "set-dataref": "sim/flightmodel/engine/ENGN_thro[0]",
                "value-min": 0,
                "value-max": 1,
            },
            "representation": {
                "type": "slider-icon",
                "slider-icon": {
                    "dataref": "sim/flightmodel/engine/ENGN_thro[0]",
                    "value-min": 0,
                    "value-max": 1,
                    "label": "POWER",
                    "fill-color": "cyan",
                    "orientation": "vertical",
                },
            },
        },
        "Mixture Slider (web deck)": {
            "index": 1,
            "name": "mixture_slider",
            "span": [1, 3],
            "activation": {
                "type": "slider",
                "set-dataref": "sim/flightmodel/engine/ENGN_mixt[0]",
                "value-min": 0,
                "value-max": 1,
            },
            "representation": {
                "type": "slider-icon",
                "slider-icon": {
                    "dataref": "sim/flightmodel/engine/ENGN_mixt[0]",
                    "value-min": 0,
                    "value-max": 1,
                    "label": "MIXTURE",
                    "fill-color": "#ff8c00",
                    "orientation": "vertical",
                },
            },
        },
    },
    "Gauges": {
        "Tachometer (0–2700 RPM)": {
            "index": 0,
            "name": "gauge_rpm",
            "activation": {"type": "none"},
            "representation": {
                "type": "gauge",
                "label": "RPM ×100",
                "label-size": 11,
                "formula": "${sim/cockpit2/engine/indicators/prop_speed_rpm[0]} 0.1 *",
                "gauge": {
                    "tick-from": -120,
                    "tick-to": 120,
                    "ticks": 7,
                    "tick-labels": ["0", "5", "10", "15", "20", "25", "30"],
                    "needle-color": "white",
                },
            },
        },
        "Oil Temperature": {
            "index": 0,
            "name": "gauge_oil_temp",
            "activation": {"type": "none"},
            "representation": {
                "type": "gauge",
                "label": "OIL °C",
                "label-size": 11,
                "formula": "${sim/cockpit2/engine/indicators/oil_temperature_deg_C[0]}",
                "gauge": {
                    "tick-from": -120,
                    "tick-to": 120,
                    "ticks": 5,
                    "tick-labels": ["60", "90", "120", "150", "180"],
                    "needle-color": "orangered",
                },
            },
        },
        "Fuel Level Tape": {
            "index": 0,
            "name": "fuel_tape",
            "activation": {"type": "none"},
            "representation": {
                "type": "data",
                "label": "FUEL L",
                "label-size": 11,
                "dataref": "sim/cockpit2/fuel/fuel_level_left",
                "text": "${formula} GAL",
            },
        },
    },
    "Avionics": {
        "COM1 Active Frequency": {
            "index": 0,
            "name": "com1_active",
            "activation": {
                "type": "push",
                "commands": {"press": "sim/radios/com1_standby_flip"},
            },
            "representation": {
                "type": "text",
                "label": "COM 1",
                "label-size": 11,
                "text": "${sim/cockpit2/radios/actuators/com1_frequency_hz} 1e-6 * 1000 round 1000 /",
                "text-size": 18,
                "text-color": "lime",
                "text-format": "{:.3f}",
            },
        },
        "Transponder (7-seg)": {
            "index": 0,
            "name": "xpndr_code",
            "activation": {"type": "none"},
            "representation": {
                "type": "text",
                "label": "XPDR",
                "label-size": 11,
                "text": "${sim/cockpit/radios/transponder_code}",
                "text-size": 28,
                "text-font": "Seven Segment.ttf",
                "text-color": "cyan",
                "text-format": "{:04.0f}",
            },
        },
        "Altitude (feet)": {
            "index": 0,
            "name": "altitude_ft",
            "activation": {"type": "none"},
            "representation": {
                "type": "text",
                "label": "ALT ft",
                "label-size": 11,
                "text": "${sim/cockpit2/gauges/indicators/altitude_ft_pilot}",
                "text-size": 22,
                "text-color": "white",
                "text-format": "{:.0f}",
            },
        },
    },
}
