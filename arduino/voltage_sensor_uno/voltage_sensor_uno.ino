const int VOLTAGE_PIN = A0;
const int MAP_PIN = A1;
const int TACH_PIN = 2;

const int SAMPLE_COUNT = 5;
const unsigned long REPORT_INTERVAL_MS = 50;
const unsigned long TACH_MIN_PULSE_GAP_US = 2500;

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

  const int voltageRaw = readAnalogAverage(VOLTAGE_PIN);
  const int mapRaw = readAnalogAverage(MAP_PIN);
  const unsigned long pulseCount = readAndResetTachPulseCount();

  Serial.print("{\"type\":\"engine_raw\",\"voltage_pin\":\"A0\",\"voltage_raw\":");
  Serial.print(voltageRaw);
  Serial.print(",\"map_pin\":\"A1\",\"map_raw\":");
  Serial.print(mapRaw);
  Serial.print(",\"tach_pin\":\"D2\",\"tach_pulses\":");
  Serial.print(pulseCount);
  Serial.print(",\"interval_ms\":");
  Serial.print(REPORT_INTERVAL_MS);
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

unsigned long readAndResetTachPulseCount() {
  noInterrupts();
  const unsigned long count = tachPulseCount;
  tachPulseCount = 0;
  interrupts();
  return count;
}

void countTachPulse() {
  const unsigned long now = micros();
  if (now - lastTachPulseUs < TACH_MIN_PULSE_GAP_US) {
    return;
  }

  lastTachPulseUs = now;
  tachPulseCount++;
}
