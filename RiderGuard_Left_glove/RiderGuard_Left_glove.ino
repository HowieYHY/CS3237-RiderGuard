
#include <Wire.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <WiFi.h>
#include "ESP32MQTTClient.h"
#include "esp_idf_version.h"

// ---------------- MQTT SETTINGS ----------------
const char *ssid = "XXX";
const char *pass = "XXX";

// Use your laptop's reachable IPv4 address
char *server = "mqtt://<insert IP>:1883";

char *publishTopic = "riderguard/leftGloveMPU6050";

ESP32MQTTClient mqttClient;

const unsigned long MQTT_INTERVAL_MS = 500;
unsigned long lastMqttPublish = 0;

// ---------------- PIN DEFINITIONS ----------------
#define MPU_ADDR       0x68
#define TRIG_PIN       5
#define ECHO_PIN       18
#define MOTOR_PIN      26

// ---------------- MPU6050 SETTINGS ----------------
const float ACCEL_SENSITIVITY = 16384.0;
const float GYRO_SENSITIVITY  = 131.0;
const float GRAVITY           = 9.80665;

// ---------------- OTHER SETTINGS ----------------
const float DISTANCE_THRESHOLD_CM = 30.0;
const unsigned long MOTOR_PULSE_MS = 200;

// ---------------- MAX30102 ----------------
MAX30105 particleSensor;

const byte RATE_SIZE = 4;
byte rates[RATE_SIZE];
byte rateSpot = 0;
long lastBeat = 0;

float bpm = 0;
float averageBPM = 0;
bool max30102Ready = false;

// ---------------- MOTOR STATE ----------------
bool motorActive = false;
unsigned long motorStartTime = 0;
bool previousNearObject = false;

// Read two consecutive MPU registers
int16_t read16(byte reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);

  if (Wire.endTransmission(false) != 0) {
    return 0;
  }

  if (Wire.requestFrom((uint8_t)MPU_ADDR,
                       (uint8_t)2) != 2) {
    return 0;
  }

  uint16_t highByte = Wire.read();
  uint16_t lowByte = Wire.read();

  return (int16_t)((highByte << 8) | lowByte);
}

// Write one MPU register
bool writeMPURegister(byte reg, byte value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

// Measure ultrasonic distance
float readDistanceCM() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);

  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);

  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration =
      pulseIn(ECHO_PIN, HIGH, 30000UL);

  if (duration == 0) {
    return -1.0;
  }

  return duration * 0.0343 / 2.0;
}

// Update MAX30102 heart-rate estimate
void updateHeartRate() {
  if (!max30102Ready) return;

  long irValue = particleSensor.getIR();

  // Finger/contact detection threshold is approximate.
  // Tune for your sensor and placement.
  if (irValue < 50000) {
    bpm = 0;
    averageBPM = 0;
    return;
  }

  if (checkForBeat(irValue)) {
    unsigned long now = millis();
    unsigned long delta = now - lastBeat;
    lastBeat = now;

    if (delta > 0) {
      float measuredBPM = 60000.0 / delta;

      if (measuredBPM >= 40 && measuredBPM <= 200) {
        bpm = measuredBPM;

        rates[rateSpot++] = (byte)bpm;
        rateSpot %= RATE_SIZE;

        float total = 0;
        byte count = 0;

        for (byte i = 0; i < RATE_SIZE; i++) {
          if (rates[i] > 0) {
            total += rates[i];
            count++;
          }
        }

        if (count > 0) {
          averageBPM = total / count;
        }
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  // ---------------- WIFI + MQTT ----------------
  WiFi.begin(ssid, pass);

  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("ESP32 IP address: ");
  Serial.println(WiFi.localIP());

  mqttClient.setURI(server);
  mqttClient.enableDebuggingMessages();
  mqttClient.setKeepAlive(30);
  mqttClient.loopStart();

  Serial.println("MQTT loop started");
  delay(500);

  // Shared I2C bus for MPU6050 and MAX30102
  Wire.begin(21, 22);
  Wire.setClock(100000);

  // ---------------- MPU6050 ----------------
  bool mpuOK = true;

  mpuOK &= writeMPURegister(0x6B, 0x00); // Wake
  mpuOK &= writeMPURegister(0x1C, 0x00); // +/- 2g
  mpuOK &= writeMPURegister(0x1B, 0x00); // +/- 250 deg/s

  Serial.print("MPU6050 register setup: ");
  Serial.println(mpuOK ? "OK" : "FAILED");

  // ---------------- HC-SR04 ----------------
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  // ---------------- MOTOR ----------------
  pinMode(MOTOR_PIN, OUTPUT);
  digitalWrite(MOTOR_PIN, LOW);

  // ---------------- MAX30102 ----------------
  if (particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    max30102Ready = true;

    particleSensor.setup(
      60,    // LED brightness
      4,     // sample average
      2,     // Red + IR mode
      100,   // sample rate
      411,   // pulse width
      4096   // ADC range
    );

    particleSensor.setPulseAmplitudeRed(0x1F);
    particleSensor.setPulseAmplitudeIR(0x1F);
    particleSensor.setPulseAmplitudeGreen(0);

    Serial.println("MAX30102 detected");
  } else {
    Serial.println("ERROR: MAX30102 not detected");
  }

  Serial.println(
    "Time(ms),Ax(m/s^2),Ay(m/s^2),Az(m/s^2),"
    "Gx(deg/s),Gy(deg/s),Gz(deg/s),"
    "Distance(cm),BPM,AvgBPM,Motor"
  );
}

void loop() {
  // ---------------- MPU6050 ----------------
  int16_t axRaw = read16(0x3B);
  int16_t ayRaw = read16(0x3D);
  int16_t azRaw = read16(0x3F);

  int16_t gxRaw = read16(0x43);
  int16_t gyRaw = read16(0x45);
  int16_t gzRaw = read16(0x47);

  float ax = (axRaw / ACCEL_SENSITIVITY) * GRAVITY;
  float ay = (ayRaw / ACCEL_SENSITIVITY) * GRAVITY;
  float az = (azRaw / ACCEL_SENSITIVITY) * GRAVITY;

  float gx = gxRaw / GYRO_SENSITIVITY;
  float gy = gyRaw / GYRO_SENSITIVITY;
  float gz = gzRaw / GYRO_SENSITIVITY;

  // ---------------- ULTRASONIC ----------------
  float distance = readDistanceCM();

  bool nearObject =
      distance >= 0 &&
      distance < DISTANCE_THRESHOLD_CM;

  // ---------------- HEART RATE ----------------
  updateHeartRate();

  // ---------------- MOTOR CONTROL ----------------
  // One short pulse on each transition into near-object state.
  // This avoids holding the motor on continuously.
  if (nearObject && !previousNearObject && !motorActive) {
    digitalWrite(MOTOR_PIN, HIGH);
    motorActive = true;
    motorStartTime = millis();
  }

  if (motorActive &&
      millis() - motorStartTime >= MOTOR_PULSE_MS) {
    digitalWrite(MOTOR_PIN, LOW);
    motorActive = false;
  }

  previousNearObject = nearObject;

  // ---------------- ONE-LINE OUTPUT ----------------
  Serial.print("Time(ms)=");
  Serial.print(millis());

  Serial.print(", Ax(m/s^2)=");
  Serial.print(ax, 3);

  Serial.print(", Ay(m/s^2)=");
  Serial.print(ay, 3);

  Serial.print(", Az(m/s^2)=");
  Serial.print(az, 3);

  Serial.print(", Gx(deg/s)=");
  Serial.print(gx, 3);

  Serial.print(", Gy(deg/s)=");
  Serial.print(gy, 3);

  Serial.print(", Gz(deg/s)=");
  Serial.print(gz, 3);

  Serial.print(", Distance(cm)=");
  if (distance >= 0) {
    Serial.print(distance, 2);
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", BPM=");
  if (averageBPM > 0) {
    Serial.print(bpm, 1);
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", AvgBPM=");
  if (averageBPM > 0) {
    Serial.print(averageBPM, 1);
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", Motor=");
  Serial.println(motorActive ? "ON" : "OFF");

  // ---------------- MQTT PUBLISH ----------------
  if (millis() - lastMqttPublish >= MQTT_INTERVAL_MS) {
    lastMqttPublish = millis();

    char payload[200];

    snprintf(
      payload,
      sizeof(payload),
      "{\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
      "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f,"
      "\"distance_cm\":%.2f,\"bpm\":%.1f,"
      "\"avg_bpm\":%.1f,\"motor\":\"%s\"}",
      ax, ay, az,
      gx, gy, gz,
      distance,
      bpm,
      averageBPM,
      motorActive ? "ON" : "OFF"
    );

    if (WiFi.status() == WL_CONNECTED) {
      mqttClient.publish(publishTopic, payload, 0, false);
      Serial.print("MQTT published: ");
      Serial.println(payload);
    }
  }
  delay(500);
}

void onMqttConnect(esp_mqtt_client_handle_t client)
{
    Serial.println("MQTT CONNECTED!");
}

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0)
esp_err_t handleMQTT(esp_mqtt_event_handle_t event)
{
    mqttClient.onEventCallback(event);
    return ESP_OK;
}
#else
void handleMQTT(void *handler_args, esp_event_base_t base,
                int32_t event_id, void *event_data)
{
    auto *event = static_cast<esp_mqtt_event_handle_t>(event_data);
    mqttClient.onEventCallback(event);
}
#endif