const int VOLTAGE_PIN = A0;
const int MAP_PIN = A1;
const int TACH_PIN = 2;

const int SAMPLE_COUNT = 5;
const unsigned long REPORT_INTERVAL_MS = 50;
const unsigned long TACH_MIN_PULSE_GAP_US = 8000;

// Most common "0-25V" Arduino voltage sensor modules use a 5:1 divider.
// If a multimeter says the battery is 12.60V but this reports 12.40V,
// set CALIBRATION_MULTIPLIER to 12.60 / 12.40 = 1.0161.
const float ADC_REFERENCE_VOLTS = 5.0;
const float DIVIDER_RATIO = 5.0;
const float CALIBRATION_MULTIPLIER = 0.75;

struct TachCounts {
  unsigned long accepted;
  unsigned long rejected;
};

unsigned long lastReportMs = 0;
volatile unsigned long tachPulseCount = 0;
volatile unsigned long tachRejectedCount = 0;
volatile unsigned long lastTachPulseUs = 0;

void setup() {
  Serial.begin(115200);
  pinMode(VOLTAGE_PIN, INPUT);
  pinMode(MAP_PIN, INPUT);
  pinMode(TACH_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TACH_PIN), countTachPulse, FALLING);
}

void loop() {
  const unsigned long now = millis();
  if (now - lastReportMs < REPORT_INTERVAL_MS) {
    return;
  }

  const unsigned long intervalMs = now - lastReportMs;
  lastReportMs = now;

  const int voltageRaw = readAnalogAverage(VOLTAGE_PIN);
  const float voltage = rawToBatteryVoltage(voltageRaw);
  const bool charging = voltage >= 13.2;
  const int socEstimate = estimateLeadAcidSoc(voltage);
  const int mapRaw = readAnalogAverage(MAP_PIN);
  TachCounts tachCounts = readAndResetTachCounts();

  Serial.print("{\"type\":\"engine_raw\",\"voltage_pin\":\"A0\",\"voltage_raw\":");
  Serial.print(voltageRaw);
  Serial.print(",\"voltage\":");
  Serial.print(voltage, 3);
  Serial.print(",\"charging\":");
  Serial.print(charging ? "true" : "false");
  Serial.print(",\"soc_estimate_percent\":");
  Serial.print(socEstimate);
  Serial.print(",\"map_pin\":\"A1\",\"map_raw\":");
  Serial.print(mapRaw);
  Serial.print(",\"tach_pin\":\"D2\",\"tach_pulses\":");
  Serial.print(tachCounts.accepted);
  Serial.print(",\"tach_rejected\":");
  Serial.print(tachCounts.rejected);
  Serial.print(",\"interval_ms\":");
  Serial.print(intervalMs);
  Serial.println("}");
}

int readAnalogAverage(int pin) {
  unsigned long total = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    total += analogRead(pin);
    delayMicroseconds(250);
  }

  return round(total / (float)SAMPLE_COUNT);
}

float rawToBatteryVoltage(int raw) {
  const float sensorVoltage = raw * (ADC_REFERENCE_VOLTS / 1023.0);
  return sensorVoltage * DIVIDER_RATIO * CALIBRATION_MULTIPLIER;
}

int estimateLeadAcidSoc(float voltage) {
  // Approximate resting 12V lead-acid SOC. Under load or charging this is only
  // a rough health indicator, not true state of charge.
  if (voltage >= 12.70) return 100;
  if (voltage >= 12.50) return 90;
  if (voltage >= 12.42) return 80;
  if (voltage >= 12.32) return 70;
  if (voltage >= 12.20) return 60;
  if (voltage >= 12.06) return 50;
  if (voltage >= 11.90) return 40;
  if (voltage >= 11.75) return 30;
  if (voltage >= 11.58) return 20;
  if (voltage >= 11.31) return 10;
  return 0;
}

TachCounts readAndResetTachCounts() {
  noInterrupts();
  const TachCounts counts = { tachPulseCount, tachRejectedCount };
  tachPulseCount = 0;
  tachRejectedCount = 0;
  interrupts();
  return counts;
}

void countTachPulse() {
  const unsigned long now = micros();
  if (now - lastTachPulseUs < TACH_MIN_PULSE_GAP_US) {
    tachRejectedCount++;
    return;
  }

  lastTachPulseUs = now;
  tachPulseCount++;
}
