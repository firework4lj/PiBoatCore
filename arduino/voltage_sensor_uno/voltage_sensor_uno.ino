const int VOLTAGE_PIN = A0;
const int MAP_PIN = A1;
const int TACH_PIN = 2;

const int SAMPLE_COUNT = 5;
const unsigned long REPORT_INTERVAL_MS = 50;
const unsigned long TACH_MIN_PULSE_GAP_US = 8000;

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
  const int mapRaw = readAnalogAverage(MAP_PIN);
  TachCounts tachCounts = readAndResetTachCounts();

  Serial.print("{\"type\":\"engine_raw\",\"voltage_pin\":\"A0\",\"voltage_raw\":");
  Serial.print(voltageRaw);
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
