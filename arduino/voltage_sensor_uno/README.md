# Arduino Uno Voltage Sensor

Reads a common 0-25V Arduino voltage sensor module on `A0`, a MAP sensor on
`A1`, and a tach pickup on `D2`. It prints one JSON line every 50ms over USB
serial at `115200` baud.

## Wiring

- Sensor `+` / `VCC` -> Arduino `5V`
- Sensor `-` / `GND` -> Arduino `GND`
- Voltage sensor `S` / output -> Arduino `A0`
- MAP sensor output -> Arduino `A1`
- Tach pickup -> Arduino `D2`
- Battery positive -> sensor voltage input `+`
- Battery negative -> sensor voltage input `-`

Add a small inline fuse close to the battery positive lead before running the
wire to the helm.

## Output

```json
{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,"voltage":12.647,"charging":false,"soc_estimate_percent":90,"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":1,"tach_rejected":0,"interval_ms":50}
```

## Calibration

Measure the battery with a multimeter and compare it to the serial output.

If the multimeter says `12.60V` and the sketch reports `12.40V`, set:

```cpp
const float CALIBRATION_MULTIPLIER = 1.0161;
```

because `12.60 / 12.40 = 1.0161`.
