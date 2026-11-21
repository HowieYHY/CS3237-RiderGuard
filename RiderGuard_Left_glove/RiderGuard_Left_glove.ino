#include <Wire.h>

// MAX30105 heart-rate sensor disabled for now
// #include "MAX30105.h"
// #include "heartRate.h"

#include <WiFi.h>
#include "ESP32MQTTClient.h"
#include "esp_idf_version.h"

// ---------------- MQTT SETTINGS ----------------
const char *ssid = "YOUR_WIFI_SSID";
const char *pass = "YOUR_WIFI_PASSWORD";
char *server = "mqtt://HOSTING_IP_ADDRESS:1883";

char *publishTopic = "riderguard/leftGloveMPU6050";

ESP32MQTTClient mqttClient;

const unsigned long MQTT_INTERVAL_MS = 300;
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
const float DISTANCE_THRESHOLD_CM = 300.0;
const float DETECTION_THRESHOLD_CM = 60.0;
const unsigned long MOTOR_PULSE_MS = 200;

// ---------------- MOTOR STATE ----------------
bool motorActive = false;
unsigned long motorStartTime = 0;
bool previousNearObject = false;
bool currCycleMotorOn = false;

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

void setup() {
  Serial.begin(115200);

  // ---------------- WIFI + MQTT ----------------
  WiFi.begin(ssid, password);

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

  // ---------------- I2C ----------------
  // MPU6050 remains active.
  Wire.begin(21, 22);
  Wire.setClock(100000); //I2C_SPEED_STANDARD = 100000

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

  Serial.println("MPU6050 and ultrasonic sensor active");
  Serial.println("Time(ms),Ax,Ay,Az,Gx,Gy,Gz,Distance(cm),Motor");
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
      distance < DETECTION_THRESHOLD_CM;

  // ---------------- MOTOR CONTROL ----------------
  // One short pulse on each transition into near-object state.
  // This avoids holding the motor on continuously.
  if (nearObject && !motorActive) {
    digitalWrite(MOTOR_PIN, HIGH);
    motorActive = true;
    motorStartTime = millis();
  }

  currCycleMotorOn = motorActive;

  if (!nearObject || (motorActive && millis() - motorStartTime >= MOTOR_PULSE_MS)) {
    digitalWrite(MOTOR_PIN, LOW);
    motorActive = false;
  }

  // ---------------- SERIAL OUTPUT ----------------
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
  }
  else if(distance >= DISTANCE_THRESHOLD_CM){
    Serial.print("INVALID");
  }

  Serial.print(", Motor=");
  Serial.println(currCycleMotorOn ? "ON" : "OFF");

  // ---------------- MQTT PUBLISH ----------------
  if (millis() - lastMqttPublish >= MQTT_INTERVAL_MS) {
    lastMqttPublish = millis();

    char payload[256];

    snprintf(
      payload,
      sizeof(payload),
      "{\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
      "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f,"
      "\"distance_cm\":%.2f,"
      "\"motor\":\"%s\"}",
      ax, ay, az,
      gx, gy, gz,
      distance,
      motorActive ? "ON" : "OFF"
    );

    if (WiFi.status() == WL_CONNECTED) {
      mqttClient.publish(publishTopic, payload, 0, false);

      Serial.print("MQTT published: ");
      Serial.println(payload);
    }
  }

  delay(10);
}

// Compatibility callback used by the Lab 5 ESP32MQTTClient setup
#include "esp_idf_version.h"

// ---------------- MQTT CALLBACK ----------------
void onMqttConnect(esp_mqtt_client_handle_t client) {
  Serial.println("MQTT CONNECTED!");
}

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0)

esp_err_t handleMQTT(esp_mqtt_event_handle_t event) {
  mqttClient.onEventCallback(event);
  return ESP_OK;
}

#else

void handleMQTT(
    void *handler_args,
    esp_event_base_t base,
    int32_t event_id,
    void *event_data
) {
  auto *event = static_cast<esp_mqtt_event_handle_t>(event_data);
  mqttClient.onEventCallback(event);
}

#endif