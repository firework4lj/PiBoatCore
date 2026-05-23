const int VOLTAGE_PIN = A0;
const int MAP_PIN = A1;
const int TACH_PIN = 2;

// Most common "0-25V" Arduino voltage sensor modules use a 5:1 divider.
// If a multimeter says the battery is 12.60V but this reports 12.40V,
// set CALIBRATION_MULTIPLIER to 12.60 / 12.40 = 1.0161.
const float ADC_REFERENCE_VOLTS = 5.0;
const float DIVIDER_RATIO = 5.0;
const float CALIBRATION_MULTIPLIER = 1.0;

const float MAP_MIN_VOLTS = 0.50;
const float MAP_MAX_VOLTS = 4.50;
const float MAP_MIN_KPA = 10.0;
const float MAP_MAX_KPA = 105.0;

const int SAMPLE_COUNT = 25;
const unsigned long REPORT_INTERVAL_MS = 1000;
const unsigned long TACH_MIN_PULSE_GAP_US = 2500;
const float SPARKS_PER_REVOLUTION = 0.5;

unsigned long lastReportMs = REPORT_INTERVAL_MS;
volatile unsigned long tachPulseCount = 0;
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

  lastReportMs = now;

  const float voltage = readBatteryVoltage();
  const int mapRaw = readAnalogAverage(MAP_PIN);
  const float mapVolts = adcToVolts(mapRaw);
  const float mapKpa = estimateMapKpa(mapVolts);
  const unsigned long pulseCount = readAndResetTachPulseCount();
  const float rpm = estimateRpm(pulseCount, REPORT_INTERVAL_MS);
  const bool charging = voltage >= 13.2;
  const int socEstimate = estimateLeadAcidSoc(voltage);

  Serial.print("{\"type\":\"battery_voltage\",\"pin\":\"A0\",\"voltage\":");
  Serial.print(voltage, 3);
  Serial.print(",\"charging\":");
  Serial.print(charging ? "true" : "false");
  Serial.print(",\"soc_estimate_percent\":");
  Serial.print(socEstimate);
  Serial.print(",\"map_pin\":\"A1\",\"map_raw\":");
  Serial.print(mapRaw);
  Serial.print(",\"map_voltage\":");
  Serial.print(mapVolts, 3);
  Serial.print(",\"map_kpa\":");
  Serial.print(mapKpa, 1);
  Serial.print(",\"tach_pin\":\"D2\",\"tach_pulses\":");
  Serial.print(pulseCount);
  Serial.print(",\"rpm\":");
  Serial.print(rpm, 0);
  Serial.println("}");
}

float readBatteryVoltage() {
  const float sensorVoltage = adcToVolts(readAnalogAverage(VOLTAGE_PIN));
  return sensorVoltage * DIVIDER_RATIO * CALIBRATION_MULTIPLIER;
}

int readAnalogAverage(int pin) {
  unsigned long total = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    total += analogRead(pin);
    delay(2);
  }

  return round(total / (float)SAMPLE_COUNT);
}

float adcToVolts(int raw) {
  return raw * (ADC_REFERENCE_VOLTS / 1023.0);
}

float estimateMapKpa(float volts) {
  const float constrainedVolts = constrain(volts, MAP_MIN_VOLTS, MAP_MAX_VOLTS);
  const float ratio = (constrainedVolts - MAP_MIN_VOLTS) / (MAP_MAX_VOLTS - MAP_MIN_VOLTS);
  return MAP_MIN_KPA + (ratio * (MAP_MAX_KPA - MAP_MIN_KPA));
}

unsigned long readAndResetTachPulseCount() {
  noInterrupts();
  const unsigned long count = tachPulseCount;
  tachPulseCount = 0;
  interrupts();
  return count;
}

float estimateRpm(unsigned long pulseCount, unsigned long intervalMs) {
  if (intervalMs == 0 || SPARKS_PER_REVOLUTION <= 0) {
    return 0;
  }

  const float pulsesPerSecond = pulseCount * (1000.0 / intervalMs);
  return (pulsesPerSecond * 60.0) / SPARKS_PER_REVOLUTION;
}

void countTachPulse() {
  const unsigned long now = micros();
  if (now - lastTachPulseUs < TACH_MIN_PULSE_GAP_US) {
    return;
  }

  lastTachPulseUs = now;
  tachPulseCount++;
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
