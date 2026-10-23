
#include <Arduino.h>
#include <LDS_LDROBOT_LD14P.h>
#include <math.h>

// =============== PIN DEFINITIONS ===============

// Keep these pins if they match your current wiring
const uint8_t LIDAR_TX_PIN = 17;
const uint8_t LIDAR_RX_PIN = 16;

// =============== SETTINGS ===============

// Report every 200 milliseconds
const unsigned long PRINT_INTERVAL_MS = 200;

// Reject stale scan data after 2 seconds
const unsigned long SCAN_TIMEOUT_MS = 2000;

// Distance limits (mm)
const float MIN_DISTANCE = 10.0;
const float MAX_DISTANCE = 12000.0;

// Minimum accepted signal quality
const float MIN_QUALITY = 40.0;

// =============== OBJECTS ===============

HardwareSerial LidarSerial(1);
LDS_LDROBOT_LD14P lidar;

// =============== SCAN VARIABLES ===============

// Current rotation
float nearestDistance = -1;
float nearestAngle = -1;
float nearestQuality = 0;
int validPoints = 0;

// Most recently completed rotation
float reportedDistance = -1;
float reportedAngle = -1;
float reportedQuality = 0;
int reportedPoints = 0;

bool scanAvailable = false;
unsigned long lastScanTime = 0;
unsigned long lastPrint = 0;

// =============== SERIAL CALLBACKS ===============

int lidar_serial_read_callback() {
  return LidarSerial.read();
}

size_t lidar_serial_write_callback(
  const uint8_t *buffer,
  size_t length
) {
  return LidarSerial.write(buffer, length);
}

// =============== SCAN CALLBACK ===============

void lidar_scan_point_callback(
  float angle_deg,
  float distance_mm,
  float quality,
  bool scan_completed
) {

  // One complete rotation finished
  if (scan_completed) {

    reportedDistance = nearestDistance;
    reportedAngle = nearestAngle;
    reportedQuality = nearestQuality;
    reportedPoints = validPoints;

    scanAvailable = true;
    lastScanTime = millis();

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

  // Accept measurements from all 360 degrees
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

// =============== OTHER CALLBACKS ===============

void lidar_packet_callback(
  uint8_t *packet,
  uint16_t length,
  bool scan_completed
) {
  // Not required
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

// =============== PRINT RESULTS ===============

void printLIDAR() {

  Serial.println("--------------------------");

  if (!scanAvailable ||
      millis() - lastScanTime > SCAN_TIMEOUT_MS) {

    Serial.println("Status: NO_SCAN_DATA");
    return;
  }

  Serial.print("Valid 360 Points: ");
  Serial.println(reportedPoints);

  if (reportedDistance < 0) {

    Serial.println("Status: NO_VALID_RETURN");
    return;
  }

  Serial.print("Nearest Distance: ");
  Serial.print(reportedDistance / 10.0, 2);
  Serial.println(" cm");

  Serial.print("Angle: ");
  Serial.print(reportedAngle, 2);
  Serial.println(" degrees");

  Serial.print("Quality: ");
  Serial.println(reportedQuality, 0);

  Serial.println("--------------------------");
}

// =============== SETUP ===============

void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println("RiderGuard LiDAR Starting...");
  Serial.println("360 Degree Nearest Detection");

  // Configure UART buffer
  LidarSerial.setRxBufferSize(4096);

  uint32_t baud_rate = lidar.getSerialBaudRate();

  LidarSerial.begin(
    baud_rate,
    SERIAL_8N1,
    LIDAR_RX_PIN,
    LIDAR_TX_PIN
  );

  // Register callbacks
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

  // Initialize LiDAR
  lidar.init();

  LDS::result_t result = lidar.start();

  Serial.print("LiDAR Start Result: ");
  Serial.println(lidar.resultCodeToString(result));
}

// =============== MAIN LOOP ===============

void loop() {

  // Continuously process LiDAR data
  lidar.loop();

  // Print latest scan result every 200 ms
  unsigned long currentMillis = millis();

  if (currentMillis - lastPrint >= PRINT_INTERVAL_MS) {

    lastPrint = currentMillis;

    printLIDAR();
  }
}
