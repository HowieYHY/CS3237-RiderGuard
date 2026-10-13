#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <TinyGPS++.h>

#include "ESP32MQTTClient.h"

const char *ssid = "YOUR_WIFI_NAME";
const char *pass = "YOUR_WIFI_PASSWORD";

const char *server = "mqtt://test.mosquitto.org:1883";

const char *mpuTopic   = "riderguard/helmetMPU6050";
const char *gpsTopic   = "riderguard/helmetGPS";
const char *lidarTopic = "riderguard/helmetLIDAR";

ESP32MQTTClient mqttClient;

Adafruit_MPU6050 mpu;
TinyGPSPlus gps;

HardwareSerial gpsSerial(2);
HardwareSerial lidarSerial(1);

#define MPU_SDA 21
#define MPU_SCL 22

#define GPS_RX 16
#define GPS_TX 17

#define LIDAR_RX 26
#define LIDAR_TX 27

#define GPS_BAUD 9600
#define LIDAR_BAUD 115200

unsigned long previousPublish = 0;
const unsigned long publishInterval = 1000;

void setup() {

    Serial.begin(115200);
    delay(1000);

    Wire.begin(MPU_SDA, MPU_SCL);

    if (!mpu.begin()) {
        Serial.println("MPU6050 not detected!");

        while (1) {
            delay(100);
        }
    }

    Serial.println("MPU6050 initialized!");

    gpsSerial.begin(
        GPS_BAUD,
        SERIAL_8N1,
        GPS_RX,
        GPS_TX
    );

    Serial.println("GPS UART initialized!");

    lidarSerial.begin(
        LIDAR_BAUD,
        SERIAL_8N1,
        LIDAR_RX,
        LIDAR_TX
    );

    Serial.println("LiDAR UART initialized!");

    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, pass);

    Serial.print("Connecting to WiFi");

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }

    Serial.println();
    Serial.println("WiFi connected!");

    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());--

    mqttClient.setURI(server);
    mqttClient.enableDebuggingMessages();
    mqttClient.loopStart();

    Serial.println("MQTT started!");
}

void publishMPU6050() {

    sensors_event_t a, g, temp;

    mpu.getEvent(&a, &g, &temp);

    char payload[200];

    snprintf(
        payload,
        sizeof(payload),

        "{\"ax\":%.2f,"
        "\"ay\":%.2f,"
        "\"az\":%.2f,"
        "\"gx\":%.2f,"
        "\"gy\":%.2f,"
        "\"gz\":%.2f}",

        a.acceleration.x,
        a.acceleration.y,
        a.acceleration.z,

        g.gyro.x,
        g.gyro.y,
        g.gyro.z
    );

    mqttClient.publish(
        mpuTopic,
        payload,
        0,
        false
    );

    Serial.print("MPU6050: ");
    Serial.println(payload);
}

void publishGPS() {

    if (!gps.location.isValid() ||
        gps.location.age() > 5000) {

        Serial.println("Waiting for valid GPS location...");
        return;
    }

    char payload[150];

    if (gps.speed.isValid() &&
        gps.speed.age() <= 5000) {

        snprintf(
            payload,
            sizeof(payload),

            "{\"latitude\":%.6f,"
            "\"longitude\":%.6f,"
            "\"speed_kmph\":%.2f}",

            gps.location.lat(),
            gps.location.lng(),
            gps.speed.kmph()
        );

    } else {

        snprintf(
            payload,
            sizeof(payload),

            "{\"latitude\":%.6f,"
            "\"longitude\":%.6f,"
            "\"speed_kmph\":null}",

            gps.location.lat(),
            gps.location.lng()
        );
    }

    mqttClient.publish(
        gpsTopic,
        payload,
        0,
        false
    );

    Serial.print("GPS: ");
    Serial.println(payload);
}

void publishLIDAR() {

    if (!lidarSerial.available()) {
        return;
    }

    char payload[241];

    size_t count = 0;

    while (lidarSerial.available() &&
           count < 80) {

        uint8_t data = lidarSerial.read();

        snprintf(
            payload + count * 3,
            sizeof(payload) - count * 3,
            "%02X ",
            data
        );

        count++;
    }

    payload[count * 3] = '\0';

    mqttClient.publish(
        lidarTopic,
        payload,
        0,
        false
    );

    Serial.print("LiDAR RAW: ");
    Serial.println(payload);
}

void loop() {

    while (gpsSerial.available()) {
        gps.encode(gpsSerial.read());
    }

    publishLIDAR();

    unsigned long currentMillis = millis();

    if (currentMillis - previousPublish >= publishInterval) {

        previousPublish = currentMillis;

        publishMPU6050();
        publishGPS();

        Serial.println("-------------------------");
    }

    delay(5);
}
#endif