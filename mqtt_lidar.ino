
#include <Arduino.h>
#include <WiFi.h>
#include <LDS_LDROBOT_LD14P.h>
#include "ESP32MQTTClient.h"
#include "esp_idf_version.h"
#include <math.h>

// =============== WIFI + MQTT ===============

const char *ssid = "YOUR_WIFI_NAME";
const char *pass = "YOUR_WIFI_PASSWORD";

// Replace with your laptop's IPv4 address
char *server = (char *)"mqtt://<your_ip>:1883";

char *lidarTopic = (char *)"riderguard/helmetLIDAR";

ESP32MQTTClient mqttClient;

const unsigned long MQTT_INTERVAL_MS = 200;
unsigned long lastMqttPublish = 0;

const unsigned long PRINT_INTERVAL_MS = 500;
unsigned long lastPrint = 0;

// =============== LIDAR PINS ===============

// Same pins as your latest working example
#define LIDAR_RX 16
#define LIDAR_TX 17

// =============== LIDAR SETTINGS ===============

HardwareSerial lidarSerial(1);
LDS_LDROBOT_LD14P lidar;

// Distance filtering (mm)
const float MIN_DISTANCE = 150.0;
const float MAX_DISTANCE = 12000.0;

// Quality filtering
const float MIN_QUALITY = 40.0;

// Scan freshness timeout
const unsigned long SCAN_TIMEOUT_MS = 2000;

// =============== SCAN VARIABLES ===============

// Current rotation
float nearestDistance = -1;
float nearestAngle = -1;
float nearestQuality = 0;
int validPoints = 0;

// Completed rotation
float reportedDistance = -1;
float reportedAngle = -1;
float reportedQuality = 0;
int reportedPoints = 0;

bool scanAvailable = false;
unsigned long lastScanTime = 0;

unsigned long scanSequence = 0;

// =============== SERIAL CALLBACKS ===============

int lidar_serial_read_callback() {
  return lidarSerial.read();
}

size_t lidar_serial_write_callback(
  const uint8_t *buffer,
  size_t length
) {
  return lidarSerial.write(buffer, length);
}

// =============== LIDAR SCAN CALLBACK ===============

void lidar_scan_point_callback(
  float angle_deg,
  float distance_mm,
  float quality,
  bool scan_completed
) {

  // Save completed 360-degree scan
  if (scan_completed) {

    reportedDistance = nearestDistance;
    reportedAngle = nearestAngle;
    reportedQuality = nearestQuality;
    reportedPoints = validPoints;

    scanAvailable = true;
    lastScanTime = millis();
    scanSequence++;

    // Reset for next rotation
    nearestDistance = -1;
    nearestAngle = -1;
    nearestQuality = 0;
    validPoints = 0;
  }

  // Reject invalid measurements
  if (!isfinite(angle_deg) ||
      !isfinite(distance_mm) ||
      !isfinite(quality)) {
    return;
  }

  // Accept all 360 degrees
  if (angle_deg < 0 || angle_deg >= 360) {
    return;
  }

  // Distance filtering
  if (distance_mm < MIN_DISTANCE ||
      distance_mm > MAX_DISTANCE) {
    return;
  }

  // Quality filtering
  if (quality < MIN_QUALITY) {
    return;
  }

  validPoints++;

  // Find nearest valid measurement
  if (nearestDistance < 0 ||
      distance_mm < nearestDistance) {

    nearestDistance = distance_mm;
    nearestAngle = angle_deg;
    nearestQuality = quality;
  }
}

// =============== OTHER LIDAR CALLBACKS ===============

void lidar_packet_callback(
  uint8_t *packet,
  uint16_t length,
  bool scan_completed
) {
  // Not needed
}

void lidar_info_callback(
  LDS::info_t code,
  String info
) {
  Serial.print("LiDAR info: ");
  Serial.println(info);
}

void lidar_error_callback(
  LDS::result_t code,
  String aux_info
) {
  Serial.print("LiDAR error: ");
  Serial.print(lidar.resultCodeToString(code));
  Serial.print(" - ");
  Serial.println(aux_info);
}

// =============== MQTT PUBLISH ===============

void publishLIDAR() {

  bool freshScan = (
    scanAvailable &&
    millis() - lastScanTime <= SCAN_TIMEOUT_MS
  );

  bool validScan = (
    freshScan &&
    reportedPoints > 0 &&
    reportedDistance >= 0
  );

  float distanceCM = validScan
      ? reportedDistance / 10.0f
      : -1.0f;

  float angle = validScan
      ? reportedAngle
      : -1.0f;

  float quality = validScan
      ? reportedQuality
      : 0.0f;

  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"time_ms\":%lu,"
    "\"scan_sequence\":%lu,"
    "\"scan_valid\":%s,"
    "\"distance_cm\":%.2f,"
    "\"angle_deg\":%.2f,"
    "\"quality\":%.2f,"
    "\"valid_points\":%d}",
    millis(),
    scanSequence,
    validScan ? "true" : "false",
    distanceCM,
    angle,
    quality,
    freshScan ? reportedPoints : 0
  );

  mqttClient.publish(
    lidarTopic,
    payload,
    0,
    false
  );
}

// =============== SERIAL OUTPUT ===============

void printLIDAR() {

  bool freshScan = (
    scanAvailable &&
    millis() - lastScanTime <= SCAN_TIMEOUT_MS
  );

  Serial.println();
  Serial.println("==============================");

  if (!freshScan) {
    Serial.println("NO SCAN DATA");
    return;
  }

  Serial.print("Scan Number: ");
  Serial.println(scanSequence);

  Serial.print("Valid 360 Points: ");
  Serial.println(reportedPoints);

  if (reportedDistance < 0) {
    Serial.println("NO VALID DISTANCE");
    return;
  }

  Serial.print("Nearest Distance: ");
  Serial.print(reportedDistance / 10.0f, 2);
  Serial.println(" cm");

  Serial.print("Angle: ");
  Serial.print(reportedAngle, 2);
  Serial.println(" degrees");

  Serial.print("Quality: ");
  Serial.println(reportedQuality, 0);
}

// =============== SETUP ===============

void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println("RiderGuard LiDAR + MQTT Starting");

  // ---------------- LIDAR ----------------

  lidarSerial.setRxBufferSize(4096);

  uint32_t lidarBaud = lidar.getSerialBaudRate();

  lidarSerial.begin(
    lidarBaud,
    SERIAL_8N1,
    LIDAR_RX,
    LIDAR_TX
  );

  lidar.setScanPointCallback(
    lidar_scan_point_callback
  );

  lidar.setPacketCallback(
    lidar_packet_callback
  );

  lidar.setSerialWriteCallback(
    lidar_serial_write_callback
  );

  lidar.setSerialReadCallback(
    lidar_serial_read_callback
  );

  lidar.setInfoCallback(
    lidar_info_callback
  );

  lidar.setErrorCallback(
    lidar_error_callback
  );

  lidar.init();

  LDS::result_t result = lidar.start();

  Serial.print("LiDAR start: ");
  Serial.println(lidar.resultCodeToString(result));

  // ---------------- WIFI ----------------

  WiFi.begin(ssid, pass);

  Serial.println("Connecting to Wi-Fi...");

  // ---------------- MQTT ----------------

  mqttClient.setURI(server);
  mqttClient.enableDebuggingMessages();
  mqttClient.setKeepAlive(30);
  mqttClient.loopStart();

  Serial.println("MQTT loop started");
}

// =============== MAIN LOOP ===============

void loop() {

  // Continuously process LiDAR
  lidar.loop();

  unsigned long currentMillis = millis();

  // Publish every 200 ms
  if (currentMillis - lastMqttPublish >=
      MQTT_INTERVAL_MS) {

    lastMqttPublish = currentMillis;

    if (WiFi.status() == WL_CONNECTED) {
      publishLIDAR();
    }
  }

  // Print every 500 ms
  if (currentMillis - lastPrint >=
      PRINT_INTERVAL_MS) {

    lastPrint = currentMillis;
    printLIDAR();
  }
}

// =============== MQTT CALLBACKS ===============

void onMqttConnect(esp_mqtt_client_handle_t client) {
  Serial.println("MQTT CONNECTED!");
}

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0)

esp_err_t handleMQTT(
  esp_mqtt_event_handle_t event
) {
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
  auto *event =
    static_cast<esp_mqtt_event_handle_t>(event_data);

  mqttClient.onEventCallback(event);
}

#endif

