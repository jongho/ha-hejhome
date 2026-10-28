# Hejhome (헤이홈) — Home Assistant 커스텀 통합

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

> **비공식 통합입니다.** 헤이홈(Hejhome) 및 GoQual과 관계가 없으며, 공식 지원을 받지 않습니다.
> Unofficial — not affiliated with or endorsed by Hejhome / GoQual.

헤이홈 웹 대시보드(square.hej.so)의 비공식 API를 이용해 헤이홈 기기를 Home Assistant에 연동합니다.
프로토콜은 ISC 라이선스의 Homebridge 플러그인 [`@chazepps/homebridge-hejhome`](https://github.com/chazepps/homebridge-hejhome)을 참고해 Python으로 옮겼습니다.

- 상태 수신: 헤이홈 MQTT(WebSocket) 브로커로 실시간 푸시 (`cloud_push`), 5분마다 REST로 보정
- 제어: 헤이홈 클라우드 REST
- 헤이홈 앱/허브를 그대로 유지합니다 (재페어링 불필요)

## 지원 기기

| 헤이홈 deviceType | HA 엔티티 |
|---|---|
| `SensorMo`, `SensorRadar` | 모션 binary_sensor, 배터리 |
| `SensorTh`, `SensorTh2`, `SensorRefTh*` | 온도·습도 sensor, 배터리 |
| `LightRgbw*`, `LightRgb*`, `LedStrip*` | light (HS 색상 + 화이트 모드) |
| `LightWw*` | light (밝기 + 색온도 3000–6500K) |
| `SmartButton` | event (single / double / long / unknown), 배터리 |
| `SensorDo` | 문 열림 binary_sensor |
| `SensorWater2/3`, `SensorSmoke3/4` | 누수·연기 binary_sensor |
| `Switch*`, `ZigbeeSwitch*`, 릴레이, 플러그, 멀티탭 | switch (구별) |

## 설치

### HACS (권장)

1. HACS → 오른쪽 위 ⋮ → **Custom repositories**
2. Repository에 `https://github.com/jongho/ha-hejhome`, Type은 **Integration**으로 추가
3. 목록에서 **Hejhome (헤이홈)**을 찾아 다운로드
4. HA 재시작

### 수동 설치

1. 이 저장소의 `custom_components/hejhome` 폴더를 HA 설정 디렉터리의 `custom_components/` 아래에 복사
2. HA 재시작

### 통합 추가

1. 설정 → 기기 및 서비스 → 통합 추가 → **Hejhome**
2. 헤이홈 앱에 등록된 **이메일** 입력 → 메일로 온 6자리 인증번호 + 비밀번호 입력
3. 집(family)이 여러 개면 하나 선택 (집마다 통합을 따로 추가 가능)

헤이홈 방(room) 이름은 기기의 추천 영역(suggested area)으로 들어갑니다.

## 스마트 버튼

원본 Homebridge 플러그인도 스마트 버튼의 입력 값을 확인하지 못한 상태라, 흔한 Tuya 값
(`single_click`, `double_click`, `long_press`, `click`, `press` 등)을 추정해 매핑합니다.
매핑되지 않은 값은 `unknown` 이벤트로 들어오고, `code` / `value` 속성에 원본이 남습니다.

처음 설정할 때는 **개발자 도구 → 이벤트**에서 `hejhome_button`을 구독한 뒤 버튼을 눌러 보세요.
실제 리포트(`status`)가 그대로 보이므로, 그 값을 기준으로 자동화를 만들거나 `event.py`의 `classify()`를 보정하면 됩니다.

## 세션 만료

토큰이 만료되면 저장된 비밀번호로 조용히 재로그인을 시도합니다. 헤이홈 서버가 이때 이메일 인증을 다시 요구하면
HA에 **재인증** 알림이 뜨고, 확인을 누르면 새 인증번호가 발송됩니다.

## 주의

- 공식 API가 아닙니다. 헤이홈이 웹 대시보드를 바꾸면 동작이 멈출 수 있습니다.
- 이메일·비밀번호와 세션 토큰은 HA의 config entry(`.storage/core.config_entries`)에 저장됩니다.
- 디버그 로그: `configuration.yaml`에
  ```yaml
  logger:
    logs:
      custom_components.hejhome: debug
  ```

## 테스트

```bash
pip install pytest-homeassistant-custom-component
pytest
```

## 라이선스

[MIT](LICENSE). 헤이홈 프로토콜 처리 부분은 [`@chazepps/homebridge-hejhome`](https://github.com/chazepps/homebridge-hejhome)(ISC)에서 가져왔으며, 원본 라이선스 전문은 [NOTICE](NOTICE)에 있습니다.
