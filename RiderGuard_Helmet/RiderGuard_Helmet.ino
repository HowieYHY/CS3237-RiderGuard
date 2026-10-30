#include <Arduino.h>
#include <LDS_LDROBOT_LD14P.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <Preferences.h>
#include "ESP32MQTTClient.h"
#include "esp_idf_version.h"
#include <math.h>

// ---------------- NETWORK SETTINGS ----------------
const char *ssid = "YOUR_WIFI_SSID";
const char *pass = "YOUR_WIFI_PASSWORD";
char *server = "mqtt://YOUR_IP_ADDRESS:1883";
char *publishTopic = "riderguard/location";
char *cmdTopic = "riderguard/cmd/helmet";      // latency_server.py reply, echoes seq/t_ms
char *latencyTopic = "riderguard/latency";     // round-trip results, stored by server.py
ESP32MQTTClient mqttClient;

// ---------------- LATENCY ----------------
unsigned long seq = 0;
volatile bool gotCmd = false;                  // set in MQTT callback, handled in loop()
volatile unsigned long cmdLatencyMs = 0;
volatile unsigned long cmdSeq = 0;

// ---------------- PIN DEFINITIONS ----------------
const uint8_t LIDAR_TX_PIN = 17;
const uint8_t LIDAR_RX_PIN = 16;
const uint8_t GPS_RX_PIN = 26;
const uint8_t GPS_TX_PIN = 27;

// ---------------- SETTINGS ----------------
const unsigned long REPORT_INTERVAL_MS = 1000;
const unsigned long SCAN_TIMEOUT_MS = 2000;
const unsigned long GPS_MAX_AGE_MS = 5000;
const unsigned long LOCATION_STALE_AFTER_MS = 30000;
const float MIN_DISTANCE = 10.0;
const float MAX_DISTANCE = 12000.0;
const float MIN_QUALITY = 40.0;

HardwareSerial LidarSerial(1);
HardwareSerial GPSSerial(2);
LDS_LDROBOT_LD14P lidar;
TinyGPSPlus gps;
Preferences locationPrefs;

float nearestDistance = -1, nearestAngle = -1, nearestQuality = 0;
int validPoints = 0;
float reportedDistance = -1, reportedAngle = -1, reportedQuality = 0;
int reportedPoints = 0;
bool scanAvailable = false;
unsigned long lastScanTime = 0, lastReportTime = 0;
unsigned long lastWifiAttempt = 0;
unsigned long lastLocationSave = 0;
unsigned long lastGpsByteAt = 0;
unsigned long gpsBytesReceived = 0;
char gpsNmeaBuffer[120] = {0};
char lastGpsNmeaSentence[120] = "No complete NMEA sentence received";
size_t gpsNmeaIndex = 0;
bool restoredLocation = false;

// Last accepted coordinates. GPS wins when fresh. Wi-Fi estimates can be
// supplied by an application geolocation provider through setWifiLocation().
enum LocationSource { SOURCE_NONE, SOURCE_GPS, SOURCE_STALE };
double lastLat = NAN, lastLon = NAN;
float lastAltitude = NAN, lastSpeed = NAN;
unsigned long lastLocationUpdate = 0;
LocationSource lastGoodSource = SOURCE_NONE;
void saveLastLocation() {
  if (!restoredLocation || millis() - lastLocationSave >= 60000UL) {
    locationPrefs.putDouble("lat", lastLat);
    locationPrefs.putDouble("lon", lastLon);
    locationPrefs.putFloat("alt", lastAltitude);
    locationPrefs.putFloat("speed", lastSpeed);
    locationPrefs.putBool("saved", true);
    lastLocationSave = millis();
    restoredLocation = true;
  }
}

int lidar_serial_read_callback() { return LidarSerial.read(); }
size_t lidar_serial_write_callback(const uint8_t *buffer, size_t length) {
  return LidarSerial.write(buffer, length);
}

void lidar_scan_point_callback(float angle_deg, float distance_mm,
                               float quality, bool scan_completed) {
  if (scan_completed) {
    reportedDistance = nearestDistance;
    reportedAngle = nearestAngle;
    reportedQuality = nearestQuality;
    reportedPoints = validPoints;
    scanAvailable = true;
    lastScanTime = millis();
    nearestDistance = -1;
    nearestAngle = -1;
    nearestQuality = 0;
    validPoints = 0;
  }
  if (!isfinite(angle_deg) || !isfinite(distance_mm) || !isfinite(quality) ||
      angle_deg < 0 || angle_deg >= 360 || distance_mm < MIN_DISTANCE ||
      distance_mm > MAX_DISTANCE || quality < MIN_QUALITY) return;
  validPoints++;
  if (nearestDistance < 0 || distance_mm < nearestDistance) {
    nearestDistance = distance_mm;
    nearestAngle = angle_deg;
    nearestQuality = quality;
  }
}

void lidar_packet_callback(uint8_t *, uint16_t, bool) {}
void lidar_info_callback(LDS::info_t, String info) {
  Serial.print("LiDAR info: "); Serial.println(info);
}
void lidar_error_callback(LDS::result_t code, String info) {
  Serial.print("LiDAR error: ");
  Serial.print(lidar.resultCodeToString(code));
  Serial.print(" - "); Serial.println(info);
}

bool gpsIsFresh() {
  return gps.location.isValid() && gps.location.age() <= GPS_MAX_AGE_MS;
}

LocationSource selectLocation(unsigned long now) {
  if (gpsIsFresh()) {
    lastLat = gps.location.lat();
    lastLon = gps.location.lng();
    lastAltitude = gps.altitude.isValid() ? gps.altitude.meters() : NAN;
    lastSpeed = gps.speed.isValid() ? gps.speed.kmph() : NAN;
    lastLocationUpdate = now;
    lastGoodSource = SOURCE_GPS;
    saveLastLocation();
    return SOURCE_GPS;
  }
  if (lastGoodSource != SOURCE_NONE) return SOURCE_STALE;
  return SOURCE_NONE;
}

const char *sourceName(LocationSource source) {
  switch (source) {
    case SOURCE_GPS: return "GPS";
    case SOURCE_STALE: return "STALE";
    default: return "NONE";
  }
}

void printCombinedData(LocationSource source) {
  const bool locationValid = source == SOURCE_GPS;
  const bool gpsFix = gpsIsFresh();
  const bool lidarFresh = scanAvailable && millis() - lastScanTime <= SCAN_TIMEOUT_MS;
  const bool mqttConnected = WiFi.status() == WL_CONNECTED && mqttClient.isConnected();

  Serial.println("========== RIDERGUARD ==========");
  Serial.print("GPS Status       : "); Serial.println(gpsFix ? "FIX" : "NO FIX");
  Serial.print("GPS Data         : ");
  Serial.println(gpsBytesReceived == 0 ? "NO UART DATA" : "UART DATA RECEIVED");
  Serial.print("GPS Bytes        : "); Serial.println(gpsBytesReceived);
  Serial.print("NMEA Checksums   : ");
  Serial.print(gps.passedChecksum()); Serial.print(" passed / ");
  Serial.print(gps.failedChecksum()); Serial.println(" failed");
  Serial.print("Fix Sentences    : "); Serial.println(gps.sentencesWithFix());
  Serial.print("GPS Data Age     : ");
  if (lastGpsByteAt == 0) Serial.println("Unavailable");
  else { Serial.print((millis() - lastGpsByteAt) / 1000); Serial.println(" seconds"); }
  Serial.print("Last Fix NMEA    : "); Serial.println(lastGpsNmeaSentence);
  Serial.print("HDOP             : ");
  if (gps.hdop.isValid()) Serial.println(gps.hdop.hdop(), 1); else Serial.println("Unavailable");
  Serial.print("Location Source  : "); Serial.println(sourceName(source));
  Serial.print("Location Valid   : "); Serial.println(locationValid ? "YES" : "NO");
  Serial.print("Latitude         : ");
  if (source != SOURCE_NONE) Serial.println(lastLat, 6); else Serial.println("WAITING FOR LOCATION");
  Serial.print("Longitude        : ");
  if (source != SOURCE_NONE) Serial.println(lastLon, 6); else Serial.println("WAITING FOR LOCATION");
  Serial.print("Altitude         : ");
  if (source == SOURCE_GPS && isfinite(lastAltitude)) { Serial.print(lastAltitude, 2); Serial.println(" m"); }
  else Serial.println("Unavailable");
  Serial.print("Speed            : ");
  if (source == SOURCE_GPS && isfinite(lastSpeed)) { Serial.print(lastSpeed, 2); Serial.println(" km/h"); }
  else Serial.println("Unavailable");
  Serial.print("Satellites       : ");
  if (gps.satellites.isValid()) Serial.println(gps.satellites.value()); else Serial.println("Unknown");
  Serial.print("Location Age     : ");
  if (source == SOURCE_NONE) Serial.println("Unavailable");
  else if (lastLocationUpdate == 0) Serial.println("Unknown (saved before reboot)");
  else { Serial.print((millis() - lastLocationUpdate) / 1000); Serial.println(" seconds"); }
  Serial.println();
  Serial.print("LiDAR Status     : ");
  Serial.println(!lidarFresh ? "NO SCAN DATA" : reportedDistance < 0 ? "NO VALID RETURN" : "SCANNING");
  Serial.print("Nearest Distance : ");
  if (lidarFresh && reportedDistance >= 0) { Serial.print(reportedDistance / 10.0, 2); Serial.println(" cm"); }
  else Serial.println("Unavailable");
  Serial.print("Nearest Angle: ");
  if (lidarFresh && reportedAngle >= 0) { Serial.print(reportedAngle, 2); Serial.println(" degrees"); }
  else Serial.println("Unavailable");
  Serial.println();
  Serial.print("MQTT Status      : "); Serial.println(mqttConnected ? "CONNECTED" : "DISCONNECTED");
  Serial.print("Cloud Publish    : "); Serial.println(mqttConnected ? "SUCCESS" : "PENDING");
  Serial.println("================================");
}

void publishData(LocationSource source) {
  if (WiFi.status() != WL_CONNECTED || !mqttClient.isConnected()) return;
  const bool valid = source == SOURCE_GPS;
  const bool lidarFresh = scanAvailable && millis() - lastScanTime <= SCAN_TIMEOUT_MS;
  char payload[360];
  char latitude[24], longitude[24], altitude[20], speed[20];
  char satellites[12], distance[20], angle[20];

  if (source != SOURCE_NONE) {
    snprintf(latitude, sizeof(latitude), "%.6f", lastLat);
    snprintf(longitude, sizeof(longitude), "%.6f", lastLon);
  } else {
    strcpy(latitude, "null");
    strcpy(longitude, "null");
  }
  if (valid && isfinite(lastAltitude)) snprintf(altitude, sizeof(altitude), "%.2f", lastAltitude);
  else strcpy(altitude, "null");
  if (valid && isfinite(lastSpeed)) snprintf(speed, sizeof(speed), "%.2f", lastSpeed);
  else strcpy(speed, "null");
  if (gps.satellites.isValid()) snprintf(satellites, sizeof(satellites), "%lu", (unsigned long)gps.satellites.value());
  else strcpy(satellites, "0");
  if (lidarFresh && reportedDistance >= 0) snprintf(distance, sizeof(distance), "%.2f", reportedDistance / 10.0);
  else strcpy(distance, "null");
  if (lidarFresh && reportedAngle >= 0) snprintf(angle, sizeof(angle), "%.2f", reportedAngle);
  else strcpy(angle, "null");

  const char *lidarStatus = !lidarFresh ? "NO SCAN DATA" :
                            reportedDistance < 0 ? "NO VALID RETURN" : "SCANNING";
  snprintf(payload, sizeof(payload),
    "{\"seq\":%lu,\"t_ms\":%lu,"
    "\"gps_status\":\"%s\",\"location_valid\":%s,\"latitude\":%s,\"longitude\":%s,"
    "\"altitude_m\":%s,\"speed_kmph\":%s,\"satellites\":%s,\"lidar_status\":\"%s\","
    "\"nearest_distance_cm\":%s,\"nearest_angle_deg\":%s}",
    seq++, millis(),
    gpsIsFresh() ? "FIX" : "NO FIX", valid ? "true" : "false",
    latitude, longitude, altitude, speed, satellites, lidarStatus, distance, angle);
  mqttClient.publish(publishTopic, payload, 0, false);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  locationPrefs.begin("riderguard", false);
  if (locationPrefs.getBool("saved", false)) {
    lastLat = locationPrefs.getDouble("lat", NAN);
    lastLon = locationPrefs.getDouble("lon", NAN);
    lastAltitude = locationPrefs.getFloat("alt", NAN);
    lastSpeed = locationPrefs.getFloat("speed", NAN);
    if (isfinite(lastLat) && isfinite(lastLon)) {
      lastGoodSource = SOURCE_STALE;
      restoredLocation = true;
      lastLocationSave = millis();
      Serial.println("Restored last GPS location from flash (stale until new fix).");
    }
  }
  GPSSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, pass);
  Serial.println("Connecting to Wi-Fi in background");
  lastWifiAttempt = millis();
  mqttClient.setURI(server);
  mqttClient.enableDebuggingMessages();
  mqttClient.setKeepAlive(30);
  mqttClient.loopStart();

  LidarSerial.setRxBufferSize(4096);
  LidarSerial.begin(lidar.getSerialBaudRate(), SERIAL_8N1, LIDAR_RX_PIN, LIDAR_TX_PIN);
  lidar.setScanPointCallback(lidar_scan_point_callback);
  lidar.setPacketCallback(lidar_packet_callback);
  lidar.setSerialWriteCallback(lidar_serial_write_callback);
  lidar.setSerialReadCallback(lidar_serial_read_callback);
  lidar.setInfoCallback(lidar_info_callback);
  lidar.setErrorCallback(lidar_error_callback);
  lidar.init();
  LDS::result_t result = lidar.start();
  Serial.print("LiDAR Start Result: "); Serial.println(lidar.resultCodeToString(result));
}

void loop() {
  lidar.loop();
  while (GPSSerial.available() > 0) {
    const char gpsByte = static_cast<char>(GPSSerial.read());
    gps.encode(gpsByte);
    gpsBytesReceived++;
    lastGpsByteAt = millis();
    if (gpsByte == '\n') {
      gpsNmeaBuffer[gpsNmeaIndex] = '\0';
      if (gpsNmeaIndex >= 6 &&
          (memcmp(gpsNmeaBuffer + 3, "GGA", 3) == 0 ||
           memcmp(gpsNmeaBuffer + 3, "RMC", 3) == 0)) {
        strlcpy(lastGpsNmeaSentence, gpsNmeaBuffer, sizeof(lastGpsNmeaSentence));
      }
      gpsNmeaIndex = 0;
    } else if (gpsByte != '\r') {
      if (gpsNmeaIndex < sizeof(gpsNmeaBuffer) - 1) gpsNmeaBuffer[gpsNmeaIndex++] = gpsByte;
      else gpsNmeaIndex = 0;
    }
  }
  const unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED && now - lastWifiAttempt >= 10000UL) {
    lastWifiAttempt = now;
    WiFi.reconnect();
  }
  if (now - lastReportTime >= REPORT_INTERVAL_MS) {
    lastReportTime = now;
    LocationSource source = selectLocation(now);
    publishData(source);
    printCombinedData(source);
  }

  // ---------------- CLOUD REPLY (latency) ----------------
  if (gotCmd) {
    gotCmd = false;
    Serial.print("ROUND TRIP seq "); Serial.print(cmdSeq);
    Serial.print(": "); Serial.print(cmdLatencyMs); Serial.println(" ms");

    char latencyPayload[96];
    snprintf(latencyPayload, sizeof(latencyPayload),
             "{\"node\":\"helmet\",\"seq\":%lu,\"rtt_ms\":%lu}", cmdSeq, cmdLatencyMs);
    mqttClient.publish(latencyTopic, latencyPayload, 0, false);
  }
}

void onMqttConnect(esp_mqtt_client_handle_t client) {
  Serial.println("MQTT CONNECTED!");
  if (mqttClient.isMyTurn(client)) {
    // Older library versions use (const String &payload) instead
    mqttClient.subscribe(cmdTopic, [](const std::string &payload) {
      unsigned long now = millis();
      const char *t = strstr(payload.c_str(), "\"t_ms\":");
      const char *s = strstr(payload.c_str(), "\"seq\":");
      if (t == NULL || s == NULL) return;  // reply without timestamps
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
void handleMQTT(void *, esp_event_base_t, int32_t, void *event_data) {
  mqttClient.onEventCallback(static_cast<esp_mqtt_event_handle_t>(event_data));
}
#endif
