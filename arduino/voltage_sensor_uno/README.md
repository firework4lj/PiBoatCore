# Arduino Uno Voltage Sensor

Reads a common 0-25V Arduino voltage sensor module on `A1` and prints one JSON
line per second over USB serial at `115200` baud.

## Wiring

- Sensor `+` / `VCC` -> Arduino `5V`
- Sensor `-` / `GND` -> Arduino `GND`
- Sensor `S` / output -> Arduino `A1`
- Battery positive -> sensor voltage input `+`
- Battery negative -> sensor voltage input `-`

Add a small inline fuse close to the battery positive lead before running the
wire to the helm.

## Output

```json
{"type":"battery_voltage","pin":"A1","voltage":12.647,"charging":false,"soc_estimate_percent":90}
```

## Calibration

Measure the battery with a multimeter and compare it to the serial output.

If the multimeter says `12.60V` and the sketch reports `12.40V`, set:

```cpp
const float CALIBRATION_MULTIPLIER = 1.0161;
```

because `12.60 / 12.40 = 1.0161`.
