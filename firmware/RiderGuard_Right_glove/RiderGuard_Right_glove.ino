
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <WiFi.h>
#include "ESP32MQTTClient.h"

// ---------------- WIFI / MQTT ----------------
const char *ssid = "YOUR_WIFI_SSID";
const char *password = "YOUR_WIFI_PASSWORD";
char *server = "mqtt://YOUR_LAPTOP_IP:1883";

const char *mqttTopic = "riderguard/rightGloveMPU6050";
char *cmdTopic = "riderguard/cmd/rightGlove";   // ml/predict.py reply, echoes seq/t_ms
char *latencyTopic = "riderguard/latency";      // round-trip results, stored by server.py

ESP32MQTTClient mqttClient;

// ---------------- LATENCY ----------------
unsigned long seq = 0;
volatile bool gotCmd = false;                   // set in MQTT callback, handled in loop()
volatile unsigned long cmdLatencyMs = 0;        // measured in the callback, so loop delay doesn't affect it
volatile unsigned long cmdSeq = 0;

// ---------------- PIN DEFINITIONS ----------------
#define MPU_ADDR 0x68
#define TRIG_PIN 5
#define ECHO_PIN 18
#define DS18B20_PIN 4
#define LED_PIN 19

// ---------------- MPU6050 SETTINGS ----------------
const float ACCEL_SENSITIVITY = 16384.0;
const float GYRO_SENSITIVITY = 131.0;
const float GRAVITY = 9.80665;

// ---------------- OTHER SETTINGS ----------------
const float DISTANCE_THRESHOLD_CM = 30.0;

// DS18B20
OneWire oneWire(DS18B20_PIN);
DallasTemperature tempSensor(&oneWire);

// Read two consecutive registers as a signed 16-bit value
int16_t read16(byte reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);

  if (Wire.endTransmission(false) != 0) {
    return 0;
  }

  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)2) != 2) {
    return 0;
  }

  uint16_t highByte = Wire.read();
  uint16_t lowByte = Wire.read();

  return (int16_t)((highByte << 8) | lowByte);
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // I2C for MPU6050
  Wire.begin(21, 22);

  // Wake MPU6050
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  byte mpuError = Wire.endTransmission();

  if (mpuError != 0) {
    Serial.println("ERROR: MPU6050 register write failed");
    while (true) delay(100);
  }

  // Accelerometer range: +/- 2 g
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);
  Wire.write(0x00);
  Wire.endTransmission();

  // Gyroscope range: +/- 250 degrees/second
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1B);
  Wire.write(0x00);
  Wire.endTransmission();

  // HC-SR04
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  // LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // DS18B20
  tempSensor.begin();
  Serial.print("DS18B20 devices found: ");
  Serial.println(tempSensor.getDeviceCount());
  tempSensor.setResolution(10);

  // ---------------- WIFI ----------------
  WiFi.begin(ssid, password);
  Serial.print("Connecting to hotspot");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("WiFi connected. ESP32 IP: ");
  Serial.println(WiFi.localIP());

  // ---------------- MQTT ----------------
  mqttClient.setURI(server);
  mqttClient.enableDebuggingMessages();
  mqttClient.setKeepAlive(30);
  mqttClient.loopStart();

  Serial.println("MQTT client started");

  Serial.println(
    "Time(ms),Ax,Ay,Az,Gx,Gy,Gz,Distance(cm),Temp(C),LED"
  );
}

void loop() {
  // -------- MPU6050 accelerometer raw values --------
  int16_t axRaw = read16(0x3B);
  int16_t ayRaw = read16(0x3D);
  int16_t azRaw = read16(0x3F);

  // -------- MPU6050 gyroscope raw values --------
  int16_t gxRaw = read16(0x43);
  int16_t gyRaw = read16(0x45);
  int16_t gzRaw = read16(0x47);

  // Convert acceleration to m/s^2
  float ax = (axRaw / ACCEL_SENSITIVITY) * GRAVITY;
  float ay = (ayRaw / ACCEL_SENSITIVITY) * GRAVITY;
  float az = (azRaw / ACCEL_SENSITIVITY) * GRAVITY;

  // Convert angular velocity to degrees/second
  float gx = gxRaw / GYRO_SENSITIVITY;
  float gy = gyRaw / GYRO_SENSITIVITY;
  float gz = gzRaw / GYRO_SENSITIVITY;

  // -------- HC-SR04 distance --------
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000UL);
  float distance = -1.0;

  if (duration > 0) {
    distance = duration * 0.0343 / 2.0;
  }

  // -------- DS18B20 temperature --------
  tempSensor.requestTemperatures();
  float temperature = tempSensor.getTempCByIndex(0);

  bool temperatureValid =
      temperature != DEVICE_DISCONNECTED_C &&
      temperature > -55.0 &&
      temperature <= 125.0;

  // -------- LED control --------
  bool nearObject =
      distance >= 0.0 &&
      distance < DISTANCE_THRESHOLD_CM;

  digitalWrite(LED_PIN, nearObject ? HIGH : LOW);

  // -------- Serial output --------
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
  if (distance >= 0.0) {
    Serial.print(distance, 2);
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", Temp(C)=");
  if (temperatureValid) {
    Serial.print(temperature, 2);
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", LED=");
  Serial.println(nearObject ? "ON" : "OFF");

  // -------- MQTT JSON payload --------
  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"seq\":%lu,\"t_ms\":%lu,"
    "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
    "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f,"
    "\"distance_cm\":%.2f,\"temperature_c\":%.2f,"
    "\"temperature_valid\":%s,\"led\":\"%s\"}",
    seq++, millis(),
    ax, ay, az,
    gx, gy, gz,
    distance,
    temperatureValid ? temperature : -127.0,
    temperatureValid ? "true" : "false",
    nearObject ? "ON" : "OFF"
  );

  mqttClient.publish(mqttTopic, payload, 0, false);

  Serial.print("MQTT publish attempted: ");
  Serial.println(payload);

  // -------- Cloud reply (latency) --------
  if (gotCmd) {
    gotCmd = false;
    Serial.print("ROUND TRIP seq ");
    Serial.print(cmdSeq);
    Serial.print(": ");
    Serial.print(cmdLatencyMs);
    Serial.println(" ms");

    char latencyPayload[96];
    snprintf(latencyPayload, sizeof(latencyPayload),
             "{\"node\":\"rightGlove\",\"seq\":%lu,\"rtt_ms\":%lu}", cmdSeq, cmdLatencyMs);
    mqttClient.publish(latencyTopic, latencyPayload, 0, false);
  }

  delay(500);
}

// Compatibility callback used by the Lab 5 ESP32MQTTClient setup
#include "esp_idf_version.h"

void onMqttConnect(esp_mqtt_client_handle_t client) {
  Serial.println("MQTT CONNECTED!");

  if (mqttClient.isMyTurn(client)) {
    // Older library versions use (const String &payload) instead
    mqttClient.subscribe(cmdTopic, [](const std::string &payload) {
      unsigned long now = millis();
      const char *t = strstr(payload.c_str(), "\"t_ms\":");
      const char *s = strstr(payload.c_str(), "\"seq\":");
      if (t == NULL || s == NULL) {
        return;  // reply without timestamps
      }
      cmdLatencyMs = now - strtoul(t + 7, NULL, 10);
      cmdSeq = strtoul(s + 6, NULL, 10);
      gotCmd = true;
    });
  }
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
