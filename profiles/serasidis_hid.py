"""Prepare a CubeMX STM32F103 USB CDC application for Serasidis HID BL 2.2.

The bootloader occupies 2 KiB, launches applications at 0x08000800, leaves
the F1 PLL running at 72 MHz, and recognizes the Arduino upload handshake
(DTR toggles, "1EAF", then 0x424C in BKP_DR4).  CubeMX does not know any of
those board/bootloader contracts, so editor upload tasks alone are not enough.
"""
import re
from pathlib import Path

from fileio import read_text, write_text


def _write_changed(path: Path, old: str, new: str, dry_run: bool):
    if new != old:
        write_text(path, new, dry_run)


def _insert_user_block(text: str, block: str, marker: str, body: str) -> str:
    if marker in text:
        return text
    end = f"/* USER CODE END {block} */"
    if end not in text:
        raise ValueError(f"CubeMX USER CODE block '{block}' was not found")
    snippet = f"/* STM32_INIT_{marker}_BEGIN */\n{body.rstrip()}\n/* STM32_INIT_{marker}_END */\n\n"
    return text.replace(end, snippet + end, 1)


def _configure_linker(project_dir: Path, dry_run: bool):
    candidates = list(project_dir.glob("*FLASH*.ld")) + list(project_dir.glob("*.ld"))
    for path in dict.fromkeys(candidates):
        old = read_text(path)
        if re.search(r"FLASH\s*\(rx\).*ORIGIN\s*=\s*0x08000800", old, re.I):
            return
        pattern = re.compile(
            r"(FLASH\s*\(rx\)\s*:\s*ORIGIN\s*=\s*)0x08000000"
            r"(\s*,\s*LENGTH\s*=\s*)(\d+)K", re.I,
        )
        match = pattern.search(old)
        if not match:
            continue
        total_kib = int(match.group(3))
        if total_kib <= 2:
            raise RuntimeError(f"FLASH region in {path} is too small for a 2 KiB bootloader")
        new = pattern.sub(
            lambda m: f"{m.group(1)}0x08000800{m.group(2)}{total_kib - 2}K",
            old, count=1,
        )
        _write_changed(path, old, new, dry_run)
        return
    raise RuntimeError("could not find a linker FLASH region starting at 0x08000000")


def _configure_vector_table(project_dir: Path, dry_run: bool):
    system_files = list((project_dir / "Core" / "Src").glob("system_stm32f1xx.c"))
    if not system_files:
        raise RuntimeError("Core/Src/system_stm32f1xx.c was not found")
    path = system_files[0]
    old = read_text(path)
    new, count = re.subn(
        r"(#define\s+VECT_TAB_BASE_ADDRESS\s+FLASH_BASE[\s\S]*?"
        r"#define\s+VECT_TAB_OFFSET\s+)0x[0-9A-Fa-f]+U",
        r"\g<1>0x00000800U", old, count=1,
    )
    if count != 1:
        raise RuntimeError("VECT_TAB_OFFSET definition was not found")
    _write_changed(path, old, new, dry_run)

    cmake = project_dir / "CMakeLists.txt"
    old = read_text(cmake)
    if "target_compile_definitions(stm32cubemx INTERFACE USER_VECT_TAB_ADDRESS)" not in old:
        anchor = "add_subdirectory(cmake/stm32cubemx)"
        if anchor not in old:
            raise RuntimeError("CubeMX stm32cubemx add_subdirectory was not found")
        addition = (
            anchor + "\n\n"
            "# Serasidis HID bootloader: application vectors start after its 2 KiB image.\n"
            "target_compile_definitions(stm32cubemx INTERFACE USER_VECT_TAB_ADDRESS)"
        )
        new = old.replace(anchor, addition, 1)
        _write_changed(cmake, old, new, dry_run)


def _configure_clock(project_dir: Path, dry_run: bool):
    main = project_dir / "Core" / "Src" / "main.c"
    old = read_text(main)
    replacements = {
        "RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL6;":
            "RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;",
        "HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1)":
            "HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2)",
        "PeriphClkInit.UsbClockSelection = RCC_USBCLKSOURCE_PLL;":
            "PeriphClkInit.UsbClockSelection = RCC_USBCLKSOURCE_PLL_DIV1_5;",
    }
    new = old
    for before, after in replacements.items():
        if before in new:
            new = new.replace(before, after, 1)
        elif after not in new:
            raise RuntimeError(f"expected CubeMX clock statement not found: {before}")
    _write_changed(main, old, new, dry_run)

    for ioc in project_dir.glob("*.ioc"):
        old = read_text(ioc)
        values = {
            "RCC.ADCFreqValue": "12000000",
            "RCC.AHBFreq_Value": "72000000",
            "RCC.APB1Freq_Value": "36000000",
            "RCC.APB1TimFreq_Value": "72000000",
            "RCC.APB2Freq_Value": "72000000",
            "RCC.APB2TimFreq_Value": "72000000",
            "RCC.FCLKCortexFreq_Value": "72000000",
            "RCC.HCLKFreq_Value": "72000000",
            "RCC.MCOFreq_Value": "72000000",
            "RCC.PLLCLKFreq_Value": "72000000",
            "RCC.PLLMCOFreq_Value": "36000000",
            "RCC.PLLMUL": "RCC_PLL_MUL9",
            "RCC.SYSCLKFreq_VALUE": "72000000",
            "RCC.TimSysFreq_Value": "72000000",
            "RCC.USBFreq_Value": "48000000",
        }
        new = old
        if not re.search(r"(?m)^RCC\.ADCPresc=", new):
            new = re.sub(
                r"(?m)^(RCC\.ADCFreqValue=.*)$",
                "RCC.ADCPresc=RCC_ADCPCLK2_DIV6\\n\\1", new, count=1,
            )
        else:
            new = re.sub(r"(?m)^RCC\.ADCPresc=.*$",
                         "RCC.ADCPresc=RCC_ADCPCLK2_DIV6", new, count=1)
        new = re.sub(
            r"(?m)^RCC\.IPParameters=(?!ADCPresc,)(.*)$",
            r"RCC.IPParameters=ADCPresc,\1", new, count=1,
        )
        for key, value in values.items():
            new = re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", new)
        _write_changed(ioc, old, new, dry_run)


def _configure_usb_attach(project_dir: Path, delay_ms: int, dry_run: bool):
    path = project_dir / "USB_DEVICE" / "App" / "usb_device.c"
    old = read_text(path)
    new = old
    if "usb_device_initialized" not in new:
        new = _insert_user_block(new, "PV", "HID_USB_STATE", f"""#define USB_REENUMERATION_DELAY_MS  {delay_ms}U

static uint8_t usb_device_initialized;""")
        new = _insert_user_block(new, "USB_DEVICE_Init_PreTreatment", "HID_USB_DETACH", """if (usb_device_initialized != 0U)
  {
    return;
  }

  GPIO_InitTypeDef gpio = {0};
  __HAL_RCC_GPIOA_CLK_ENABLE();
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_12, GPIO_PIN_RESET);
  gpio.Pin = GPIO_PIN_12;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &gpio);
  HAL_Delay(USB_REENUMERATION_DELAY_MS);""")
        new = _insert_user_block(new, "USB_DEVICE_Init_PostTreatment", "HID_USB_ATTACH", """HAL_GPIO_DeInit(GPIOA, GPIO_PIN_12);
  usb_device_initialized = 1U;""")
    else:
        new = re.sub(
            r"#define\s+USB_REENUMERATION_DELAY_MS\s+\d+U",
            f"#define USB_REENUMERATION_DELAY_MS  {delay_ms}U", new, count=1,
        )
    _write_changed(path, old, new, dry_run)


def _configure_early_usb(project_dir: Path, dry_run: bool):
    path = project_dir / "Core" / "Src" / "main.c"
    old = read_text(path)
    user2 = re.search(r"/\* USER CODE BEGIN 2 \*/(.*?)/\* USER CODE END 2 \*/", old, re.S)
    if not user2:
        raise RuntimeError("main.c USER CODE block 2 was not found")
    before_user2 = old[:user2.start()]
    new = old
    if "MX_USB_DEVICE_Init();" not in before_user2 and "MX_USB_DEVICE_Init();" not in user2.group(1):
        if '#include "usb_device.h"' not in new:
            new = _insert_user_block(new, "Includes", "HID_USB_INCLUDE", '#include "usb_device.h"')
        new = _insert_user_block(new, "2", "HID_USB_EARLY_INIT", """/* Start CDC before the RTOS scheduler. The generated default-task call is
   * harmless because MX_USB_DEVICE_Init() is idempotent. */
  MX_USB_DEVICE_Init();""")
    _write_changed(path, old, new, dry_run)


def _configure_cdc_reset(project_dir: Path, dry_run: bool):
    path = project_dir / "USB_DEVICE" / "App" / "usbd_cdc_if.c"
    old = read_text(path)
    new = old
    if "HID_Bootloader_Reset" in new:
        # Correct older generated setup that used the wrong backup register.
        new = new.replace("BKP->DR10 = HID_BOOTLOADER_BKP_MAGIC;",
                          "BKP->DR4 = HID_BOOTLOADER_BKP_MAGIC;")
        _write_changed(path, old, new, dry_run)
        return

    new = _insert_user_block(new, "PRIVATE_DEFINES", "HID_CDC_DEFINES", """#define HID_BOOTLOADER_DTR_EVENTS_REQUIRED  4U
#define HID_BOOTLOADER_BKP_MAGIC            0x424CU""")
    new = _insert_user_block(new, "PRIVATE_VARIABLES", "HID_CDC_STATE", """static uint8_t hid_bootloader_dtr_events;
static uint8_t hid_bootloader_magic_index;""")
    new = _insert_user_block(new, "PRIVATE_FUNCTIONS_DECLARATION", "HID_CDC_DECL", "static void HID_Bootloader_Reset(void);")

    case_anchor = "case CDC_SET_CONTROL_LINE_STATE:"
    if case_anchor not in new:
        raise RuntimeError("CDC_SET_CONTROL_LINE_STATE handler was not found")
    new = new.replace(case_anchor, case_anchor + "\n"
        "      /* STM32_INIT_HID_CDC_DTR_BEGIN */\n"
        "      if (hid_bootloader_dtr_events < UINT8_MAX)\n"
        "      {\n"
        "        hid_bootloader_dtr_events++;\n"
        "      }\n"
        "      /* STM32_INIT_HID_CDC_DTR_END */", 1)

    receive_marker = "/* USER CODE BEGIN 6 */"
    receive_body = """/* STM32_INIT_HID_CDC_MAGIC_BEGIN */
  static const uint8_t hid_magic[] = {'1', 'E', 'A', 'F'};
  if (hid_bootloader_dtr_events >= HID_BOOTLOADER_DTR_EVENTS_REQUIRED)
  {
    for (uint32_t i = 0U; i < *Len; i++)
    {
      if (Buf[i] == hid_magic[hid_bootloader_magic_index])
      {
        if (++hid_bootloader_magic_index == sizeof(hid_magic))
        {
          HID_Bootloader_Reset();
        }
      }
      else
      {
        hid_bootloader_magic_index = (Buf[i] == hid_magic[0]) ? 1U : 0U;
      }
    }
  }
  /* STM32_INIT_HID_CDC_MAGIC_END */"""
    if receive_marker not in new:
        raise RuntimeError("CDC receive USER CODE block was not found")
    new = new.replace(receive_marker, receive_marker + "\n" + receive_body, 1)

    helper = """static void HID_Bootloader_Reset(void)
{
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_RCC_BKP_CLK_ENABLE();
  SET_BIT(PWR->CR, PWR_CR_DBP);
  while ((PWR->CR & PWR_CR_DBP) == 0U) {}
  BKP->DR4 = HID_BOOTLOADER_BKP_MAGIC;
  __DSB();
  __ISB();
  NVIC_SystemReset();
}"""
    new = _insert_user_block(new, "PRIVATE_FUNCTIONS_IMPLEMENTATION", "HID_CDC_RESET", helper)
    _write_changed(path, old, new, dry_run)


def configure_serasidis_f103(project_dir: Path, device, delay_ms: int,
                             dry_run: bool, required: bool = False):
    print("\nPreparing Serasidis HID bootloader application")
    if not device or not str(device).startswith("STM32F103"):
        message = f"Serasidis automatic app setup currently supports STM32F103 only (detected: {device})"
        if required:
            raise RuntimeError(message)
        print(f"  WARNING: {message}; skipping firmware changes.")
        return
    if not 0 <= delay_ms <= 10000:
        raise RuntimeError("--hid-usb-delay-ms must be between 0 and 10000")

    required_files = [
        project_dir / "USB_DEVICE" / "App" / "usb_device.c",
        project_dir / "USB_DEVICE" / "App" / "usbd_cdc_if.c",
    ]
    missing = [str(path.relative_to(project_dir)) for path in required_files if not path.is_file()]
    if missing:
        raise RuntimeError(
            "USB CDC sources are missing (" + ", ".join(missing) + "). "
            "In CubeMX select USB Device FS and Communication Device Class "
            "(Virtual Port Com), generate code, then rerun stm32-vscode-init."
        )

    _configure_linker(project_dir, dry_run)
    _configure_vector_table(project_dir, dry_run)
    _configure_clock(project_dir, dry_run)
    _configure_usb_attach(project_dir, delay_ms, dry_run)
    _configure_early_usb(project_dir, dry_run)
    _configure_cdc_reset(project_dir, dry_run)
    print("  HID app setup: 0x08000800, 72 MHz/USB 48 MHz, CDC reset handshake, clean D+ attach")
