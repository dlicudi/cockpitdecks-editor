"""Button templates for the Gallery Action Bar."""

TEMPLATES = {
    "Switches": {
        "Round Toggle (Blue)": {
            "activation": {"type": "onoff", "command": "sim/lights/landing_lights_on"},
            "representation": {
                "type": "switch",
                "label": "LANDING",
                "style": "round",
                "handle-fill-color": "deepskyblue",
                "tick-labels": [{"-label": "OFF"}, {"-label": "ON"}]
            }
        },
        "Circular Magnetos": {
            "activation": {"type": "encoder-toggle", "command": "sim/magnetos"},
            "representation": {
                "type": "circular-switch",
                "label": "MAGNETOS",
                "tick-labels": [{"-label": "OFF"}, {"-label": "L"}, {"-label": "R"}, {"-label": "BOTH"}, {"-label": "START"}]
            }
        },
        "Heavy Bat Switch": {
            "activation": {"type": "onoff", "command": "sim/electrical/battery_1"},
            "representation": {
                "type": "switch",
                "label": "BAT 1",
                "style": "rect",
                "handle-fill-color": "red"
            }
        }
    },
    "Annunciators": {
        "Master Caution (Blinking)": {
            "representation": {
                "type": "annunciator-animate",
                "label": "CAUTION",
                "animation": "blink",
                "annunciator": {
                    "model": "A",
                    "parts": [{
                        "color": "red",
                        "text": "MASTER\nCAUTION",
                        "text-size": 25
                    }]
                }
            }
        },
        "Landing Gear (Vivisun)": {
            "representation": {
                "type": "annunciator",
                "label": "GEAR",
                "annunciator": {
                    "model": "B",
                    "parts": [
                        {"color": "green", "text": "SAFE", "text-size": 20},
                        {"color": "red", "text": "UNSAFE", "text-size": 18}
                    ]
                }
            }
        },
        "Fire Handle (Red/Orange)": {
            "representation": {
                "type": "annunciator",
                "label": "FIRE",
                "annunciator": {
                    "model": "C",
                    "parts": [{"color": "orange", "text": "PULL", "text-size": 40}]
                }
            }
        }
    },
    "Gauges": {
        "Engine RPM (0-2700)": {
            "representation": {
                "type": "gauge",
                "label": "RPM",
                "formula": "1200",
                "gauge": {
                    "tick-from": -120,
                    "tick-to": 120,
                    "ticks": 6,
                    "tick-labels": ["0", "5", "10", "15", "20", "25", "30"],
                    "needle-color": "white"
                }
            }
        },
        "Oil Pressure (PSI)": {
            "representation": {
                "type": "gauge",
                "label": "OIL PSI",
                "formula": "65",
                "gauge": {
                    "tick-from": 0,
                    "tick-to": 300,
                    "tick-color": "yellow",
                    "needle-color": "orange"
                }
            }
        },
        "Horizontal Fuel Tape": {
            "representation": {
                "type": "data",
                "label": "FUEL L",
                "dataref": "sim/cockpit2/fuel/fuel_level_left",
                "text": "GAL: ${formula}"
            }
        }
    },
    "Avionics": {
        "COM1 Toggle": {
            "activation": {"type": "encoder-toggle", "command": "sim/radios/com1_standby_flip"},
            "representation": {
                "type": "text",
                "label": "COM 1",
                "text": "122.80",
                "text-color": "lime"
            }
        },
        "Transponder (Squawk)": {
            "representation": {
                "type": "text",
                "label": "XPDR",
                "text": "7000",
                "text-font": "Seven Segment.ttf",
                "text-color": "cyan"
            }
        }
    }
}
