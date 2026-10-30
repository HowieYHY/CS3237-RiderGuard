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

// Use your laptop's reachable IPv4 address
char *server = "mqtt://YOUR_LAPTOP_IP:1883";

char *publishTopic = "riderguard/leftGloveMPU6050";
char *cmdTopic = "riderguard/cmd/leftGlove";    // ml/predict.py reply, echoes seq/t_ms
char *latencyTopic = "riderguard/latency";       // round-trip results, stored by server.py

ESP32MQTTClient mqttClient;

const unsigned long MQTT_INTERVAL_MS = 500;
unsigned long lastMqttPublish = 0;

// ---------------- LATENCY ----------------
unsigned long seq = 0;
volatile bool gotCmd = false;              // set in MQTT callback, handled in loop()
volatile unsigned long cmdLatencyMs = 0;
volatile unsigned long cmdSeq = 0;
volatile bool cmdVibrate = false;

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

// ---------------- MAX30102 DISABLED ----------------
// MAX30105 sensor;

// const byte RATE_SIZE = 5;
// byte rates[RATE_SIZE] = {0};

// byte rateSpot = 0;
// byte validBeats = 0;

// unsigned long lastBeat = 0;
// unsigned long lastHRPrint = 0;

// float bpm = 0;
// int averageBPM = 0;

// long irValue = 0;
// bool max30102Ready = false;
// bool fingerDetected = false;

// const float MIN_BPM = 45.0;
// const float MAX_BPM = 180.0;
// const long IR_FINGER_THRESHOLD = 50000;

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

// ==================================================
// MAX30102 HEART-RATE FUNCTION DISABLED
// ==================================================

// void updateHeartRate() {
//   if (!max30102Ready) {
//     fingerDetected = false;
//     bpm = 0;
//     averageBPM = 0;
//     return;
//   }
//
//   irValue = sensor.getIR();
//
//   fingerDetected = (irValue >= IR_FINGER_THRESHOLD);
//
//   if (!fingerDetected) {
//     bpm = 0;
//     averageBPM = 0;
//
//     lastBeat = 0;
//     rateSpot = 0;
//     validBeats = 0;
//
//     for (byte i = 0; i < RATE_SIZE; i++) {
//       rates[i] = 0;
//     }
//
//     return;
//   }
//
//   if (checkForBeat(irValue)) {
//     unsigned long now = millis();
//
//     if (lastBeat == 0) {
//       lastBeat = now;
//       return;
//     }
//
//     unsigned long timeBetweenBeats = now - lastBeat;
//     lastBeat = now;
//
//     if (timeBetweenBeats == 0) {
//       return;
//     }
//
//     float measuredBPM = 60000.0 / timeBetweenBeats;
//
//     if (measuredBPM >= MIN_BPM &&
//         measuredBPM <= MAX_BPM) {
//
//       bpm = measuredBPM;
//       rates[rateSpot] = (byte)measuredBPM;
//
//       rateSpot++;
//
//       if (rateSpot >= RATE_SIZE) {
//         rateSpot = 0;
//       }
//
//       if (validBeats < RATE_SIZE) {
//         validBeats++;
//       }
//
//       if (validBeats >= RATE_SIZE) {
//         int total = 0;
//
//         for (byte i = 0; i < RATE_SIZE; i++) {
//           total += rates[i];
//         }
//
//         averageBPM = total / RATE_SIZE;
//       }
//     }
//   }
//
//   if (lastBeat != 0 &&
//       millis() - lastBeat > 10000UL) {
//
//     bpm = 0;
//     averageBPM = 0;
//
//     lastBeat = 0;
//     rateSpot = 0;
//     validBeats = 0;
//
//     for (byte i = 0; i < RATE_SIZE; i++) {
//       rates[i] = 0;
//     }
//   }
// }

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

  // ---------------- I2C ----------------
  // MPU6050 remains active.
  // MAX30102 is not initialized.
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

  // ---------------- MAX30102 INITIALIZATION DISABLED ----------------

  // if (sensor.begin(Wire, I2C_SPEED_FAST)) {
  //   max30102Ready = true;
  //
  //   sensor.setup();
  //   sensor.setPulseAmplitudeRed(0);
  //
  //   Serial.println("MAX30102 detected");
  //   Serial.println("Place your finger on the sensor...");
  // } else {
  //   max30102Ready = false;
  //   Serial.println("ERROR: MAX30102 not detected");
  // }

  Serial.println("MAX30102 disabled");
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
      distance < DISTANCE_THRESHOLD_CM;

  // ---------------- HEART RATE DISABLED ----------------
  // updateHeartRate();

  // The MAX30102 heart-rate debug output is disabled.
  //
  // if (millis() - lastHRPrint >= 1000) {
  //   lastHRPrint = millis();
  //
  //   if (!max30102Ready) {
  //     Serial.println("MAX30102 unavailable");
  //   }
  //   else if (!fingerDetected) {
  //     Serial.print("Waiting for finger... IR=");
  //     Serial.println(irValue);
  //   }
  //   else if (averageBPM == 0) {
  //     Serial.print("Measuring... keep your finger still. IR=");
  //     Serial.print(irValue);
  //     Serial.print(", Valid beats=");
  //     Serial.println(validBeats);
  //   }
  //   else {
  //     Serial.print("Heart Rate: ");
  //     Serial.print(averageBPM);
  //     Serial.print(" BPM | Current BPM: ");
  //     Serial.print(bpm, 1);
  //     Serial.print(" | IR: ");
  //     Serial.println(irValue);
  //   }
  // }

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

  // ---------------- CLOUD REPLY (latency + ML warning) ----------------
  if (gotCmd) {
    gotCmd = false;

    Serial.print("ROUND TRIP seq ");
    Serial.print(cmdSeq);
    Serial.print(": ");
    Serial.print(cmdLatencyMs);
    Serial.println(" ms");

    char latencyPayload[96];
    snprintf(latencyPayload, sizeof(latencyPayload),
             "{\"node\":\"leftGlove\",\"seq\":%lu,\"rtt_ms\":%lu}",
             cmdSeq, cmdLatencyMs);
    mqttClient.publish(latencyTopic, latencyPayload, 0, false);

    if (cmdVibrate && !motorActive) {
      digitalWrite(MOTOR_PIN, HIGH);
      motorActive = true;
      motorStartTime = millis();
    }
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
  } else {
    Serial.print("INVALID");
  }

  Serial.print(", Motor=");
  Serial.println(motorActive ? "ON" : "OFF");

  // ---------------- MQTT PUBLISH ----------------
  if (millis() - lastMqttPublish >= MQTT_INTERVAL_MS) {
    lastMqttPublish = millis();

    char payload[256];

    // BPM and avg_bpm have been removed because
    // the MAX30102 heart-rate sensor is disabled.
    snprintf(
      payload,
      sizeof(payload),
      "{\"seq\":%lu,\"t_ms\":%lu,"
      "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
      "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f,"
      "\"distance_cm\":%.2f,"
      "\"motor\":\"%s\"}",
      seq++, millis(),
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

// ---------------- MQTT CALLBACK ----------------
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
      cmdVibrate = strstr(payload.c_str(), "\"vibrate\"") != NULL;
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