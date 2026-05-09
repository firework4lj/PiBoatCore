const int VOLTAGE_PIN = A0;

// Most common "0-25V" Arduino voltage sensor modules use a 5:1 divider.
// If a multimeter says the battery is 12.60V but this reports 12.40V,
// set CALIBRATION_MULTIPLIER to 12.60 / 12.40 = 1.0161.
const float ADC_REFERENCE_VOLTS = 5.0;
const float DIVIDER_RATIO = 5.0;
const float CALIBRATION_MULTIPLIER = 1.0;

const int SAMPLE_COUNT = 25;
const unsigned long REPORT_INTERVAL_MS = 1000;

unsigned long lastReportMs = REPORT_INTERVAL_MS;

void setup() {
  Serial.begin(115200);
  pinMode(VOLTAGE_PIN, INPUT);
}

void loop() {
  const unsigned long now = millis();
  if (now - lastReportMs < REPORT_INTERVAL_MS) {
    return;
  }

  lastReportMs = now;

  const float voltage = readBatteryVoltage();
  const bool charging = voltage >= 13.2;
  const int socEstimate = estimateLeadAcidSoc(voltage);

  Serial.print("{\"type\":\"battery_voltage\",\"pin\":\"A0\",\"voltage\":");
  Serial.print(voltage, 3);
  Serial.print(",\"charging\":");
  Serial.print(charging ? "true" : "false");
  Serial.print(",\"soc_estimate_percent\":");
  Serial.print(socEstimate);
  Serial.println("}");
}

float readBatteryVoltage() {
  unsigned long total = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    total += analogRead(VOLTAGE_PIN);
    delay(2);
  }

  const float raw = total / (float)SAMPLE_COUNT;
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
