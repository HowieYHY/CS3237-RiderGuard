
#include <Arduino.h>
#include <WiFi.h>
#include <TinyGPSPlus.h>
#include <LDS_LDROBOT_LD14P.h>
#include "ESP32MQTTClient.h"
#include "esp_idf_version.h"

// =============== WIFI + MQTT ===============

const char *ssid = "your_wifi";
const char *pass = "your_password";

// Laptop's reachable IPv4 address
char *server = (char *)"mqtt://ipv4:1883";

char *gpsTopic = (char *)"riderguard/helmetGPS";
char *lidarTopic = (char *)"riderguard/helmetLIDAR";

ESP32MQTTClient mqttClient;

const unsigned long MQTT_INTERVAL_MS = 500;
unsigned long lastMqttPublish = 0;

// =============== PIN DEFINITIONS ===============

// GPS
#define GPS_RX 16
#define GPS_TX 17
#define GPS_BAUD 9600

// LiDAR
#define LIDAR_RX 26
#define LIDAR_TX 27

// =============== GPS SETTINGS ===============

TinyGPSPlus gps;
HardwareSerial gpsSerial(2);

// =============== LIDAR SETTINGS ===============

HardwareSerial lidarSerial(1);
LDS_LDROBOT_LD14P lidar;

// Front sector: 330 to 30 degrees
const float FRONT_ANGLE = 30.0;

// Distance filtering (mm)
const float MIN_DISTANCE = 150.0;
const float MAX_DISTANCE = 12000.0;

// Quality filtering (experimental)
const float MIN_QUALITY = 40.0;

// Proximity thresholds (mm)
const float DANGER_DISTANCE = 1000.0;
const float CAUTION_DISTANCE = 2500.0;

// LiDAR freshness timeout
const unsigned long SCAN_TIMEOUT_MS = 2000;

// =============== LIDAR VARIABLES ===============

// Accumulated scan values
float nearestDistance = MAX_DISTANCE + 1;
float nearestAngle = 0;
float nearestQuality = 0;
int validPoints = 0;

// Completed scan values
float reportedDistance = -1;
float reportedAngle = -1;
float reportedQuality = 0;
int reportedPoints = 0;

bool scanAvailable = false;
unsigned long lastScanTime = 0;

// =============== LIDAR CALLBACKS ===============

int lidar_serial_read_callback() {
  return lidarSerial.read();
}

size_t lidar_serial_write_callback(
  const uint8_t *buffer,
  size_t length
) {
  return lidarSerial.write(buffer, length);
}

void lidar_scan_point_callback(
  float angle_deg,
  float distance_mm,
  float quality,
  bool scan_completed
) {

  // Save results when a revolution completes
  if (scan_completed) {

    reportedPoints = validPoints;

    if (validPoints > 0) {
      reportedDistance = nearestDistance;
      reportedAngle = nearestAngle;
      reportedQuality = nearestQuality;
    } else {
      reportedDistance = -1;
      reportedAngle = -1;
      reportedQuality = 0;
    }

    scanAvailable = true;
    lastScanTime = millis();

    // Reset accumulation
    nearestDistance = MAX_DISTANCE + 1;
    nearestAngle = 0;
    nearestQuality = 0;
    validPoints = 0;
  }

  // Filter for front-facing measurements
  bool isFront = (
    angle_deg >= (360.0 - FRONT_ANGLE) ||
    angle_deg <= FRONT_ANGLE
  );

  if (!isFront) return;

  // Reject invalid measurement values
  if (!isfinite(distance_mm) ||
      !isfinite(angle_deg) ||
      !isfinite(quality)) {
    return;
  }

  if (distance_mm < MIN_DISTANCE ||
      distance_mm > MAX_DISTANCE) {
    return;
  }

  if (quality < MIN_QUALITY) {
    return;
  }

  validPoints++;

  // Keep nearest obstacle
  if (distance_mm < nearestDistance) {
    nearestDistance = distance_mm;
    nearestAngle = angle_deg;
    nearestQuality = quality;
  }
}

void lidar_packet_callback(
  uint8_t *packet,
  uint16_t length,
  bool scan_completed
) {
  // Not needed for this application
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

// =============== HAZARD CLASSIFICATION ===============

const char *getHazardStatus(float distance_mm) {

  if (distance_mm < 0) {
    return "NO_VALID_RETURN";
  }

  if (distance_mm <= DANGER_DISTANCE) {
    return "DANGER";
  }

  if (distance_mm <= CAUTION_DISTANCE) {
    return "CAUTION";
  }

  return "SAFE";
}

// =============== MQTT PUBLISH ===============

void publishGPS() {

  // Only use recent GPS measurements
  bool gpsValid = (
    gps.location.isValid() &&
    gps.location.age() < 2000
  );

  bool speedValid = (
    gpsValid &&
    gps.speed.isValid() &&
    gps.speed.age() < 2000
  );

  char latText[24];
  char lngText[24];
  char speedText[24];

  if (gpsValid) {
    snprintf(latText, sizeof(latText),
             "%.6f", gps.location.lat());

    snprintf(lngText, sizeof(lngText),
             "%.6f", gps.location.lng());
  } else {
    strcpy(latText, "null");
    strcpy(lngText, "null");
  }

  if (speedValid) {
    snprintf(speedText, sizeof(speedText),
             "%.2f", gps.speed.kmph());
  } else {
    strcpy(speedText, "null");
  }

  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"time_ms\":%lu,"
    "\"gps_valid\":%s,"
    "\"latitude\":%s,"
    "\"longitude\":%s,"
    "\"speed_kmph\":%s,"
    "\"satellites\":%lu}",
    millis(),
    gpsValid ? "true" : "false",
    latText,
    lngText,
    speedText,
    (unsigned long)(
      gps.satellites.isValid() &&
      gps.satellites.age() < 5000
      ? gps.satellites.value() : 0
    )
  );

  mqttClient.publish(gpsTopic, payload, 0, false);

  Serial.print("GPS MQTT: ");
  Serial.println(payload);
}

void publishLIDAR() {

  bool freshScan = (
    scanAvailable &&
    millis() - lastScanTime <= SCAN_TIMEOUT_MS
  );

  bool validObstacle = (
    freshScan &&
    reportedPoints > 0 &&
    reportedDistance >= 0
  );

  float distanceCM = validObstacle ?
      reportedDistance / 10.0 : -1.0;

  float angle = validObstacle ?
      reportedAngle : -1.0;

  float quality = validObstacle ?
      reportedQuality : 0.0;

  const char *status;

  if (!freshScan) {
    status = "NO_SCAN_DATA";
  } else {
    status = getHazardStatus(
      validObstacle ? reportedDistance : -1
    );
  }

  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"time_ms\":%lu,"
    "\"scan_valid\":%s,"
    "\"distance_cm\":%.2f,"
    "\"angle_deg\":%.2f,"
    "\"quality\":%.2f,"
    "\"valid_front_points\":%d,"
    "\"status\":\"%s\"}",
    millis(),
    validObstacle ? "true" : "false",
    distanceCM,
    angle,
    quality,
    freshScan ? reportedPoints : 0,
    status
  );

  mqttClient.publish(lidarTopic, payload, 0, false);

  Serial.print("LiDAR MQTT: ");
  Serial.println(payload);
}

// =============== SETUP ===============

void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println("RiderGuard Helmet Starting...");

  // ---------------- GPS ----------------

  gpsSerial.begin(
    GPS_BAUD,
    SERIAL_8N1,
    GPS_RX,
    GPS_TX
  );

  Serial.println("GPS initialized");

  // ---------------- LIDAR ----------------

  lidarSerial.setRxBufferSize(1024);

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

  Serial.print("LiDAR start result: ");
  Serial.println(lidar.resultCodeToString(result));

  // ---------------- WIFI ----------------

  WiFi.begin(ssid, pass);

  Serial.print("Connecting to Wi-Fi");

  unsigned long wifiStart = millis();

  while (WiFi.status() != WL_CONNECTED &&
         millis() - wifiStart < 10000) {

    // Keep processing sensors while waiting
    while (gpsSerial.available()) {
      gps.encode(gpsSerial.read());
    }

    lidar.loop();

    delay(1);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("Wi-Fi not connected");
  }

  // ---------------- MQTT ----------------

  mqttClient.setURI(server);
  mqttClient.enableDebuggingMessages();
  mqttClient.setKeepAlive(30);
  mqttClient.loopStart();

  Serial.println("MQTT loop started");
}

// =============== MAIN LOOP ===============

void loop() {

  // ---------------- GPS UPDATE ----------------

  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }

  // ---------------- LIDAR UPDATE ----------------

  lidar.loop();

  // ---------------- MQTT TIMER ----------------

  unsigned long currentMillis = millis();

  if (currentMillis - lastMqttPublish >=
      MQTT_INTERVAL_MS) {

    lastMqttPublish = currentMillis;

    if (WiFi.status() == WL_CONNECTED) {

      publishGPS();
      publishLIDAR();

    } else {
      Serial.println("Wi-Fi disconnected");
    }
  }
}

// =============== MQTT CALLBACKS ===============

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
  auto *event =
    static_cast<esp_mqtt_event_handle_t>(event_data);

  mqttClient.onEventCallback(event);
}

#endif
