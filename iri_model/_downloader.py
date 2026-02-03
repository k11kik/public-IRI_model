import requests
from datetime import datetime
from bs4 import BeautifulSoup
import numpy as np
import time
from urllib.parse import urljoin
import re
from typing import Dict, Any
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains


def _run_iri_profile_selenium(
    date_time: datetime,
    longitude: float,
    latitude: float,
    min_alt: float,
    max_alt: float,
    step_alt: float,
    model_version: str,
    output_filename: str,
    timeout: float = 60.0,
    time_type="UTC",
    coord_type="Geomagnetic",
    info=True,
) -> int:
    """
    IRIモデルを実行（Selenium）。
    成功したら 0, 失敗したら 1 を返す。
    """
    IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"
    if info:
        print("\n--- Downloading IRI model ---")

    driver = None
    try:
        # 座標補正
        if latitude < -90: latitude = -90
        if latitude > 90: latitude = 90
        if longitude < 0: longitude += 360
        if longitude > 360: longitude = longitude % 360

        # Chrome 起動設定
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("user-agent=Mozilla/5.0")

        service = Service()
        driver = webdriver.Chrome(service=service, options=opts)
        wait = WebDriverWait(driver, 40)
        driver.set_page_load_timeout(timeout)
        driver.get(IRI_BASE_URL)

        # --- 各項目を入力 ---
        if info:
            print("--- inputing parameters ---")
        
        # 値の補正（入力用）
        lat_val = max(-89.9, min(89.9, latitude))
        lon_val = longitude % 360
        min_alt_val = max(0, min(2000, min_alt))
        max_alt_val = max(0, min(2000, max_alt))
        step_alt_val = max(1, min(500, step_alt))

        def safe_fill(name, value):
            """React制御下のinputへの値設定と表示"""
            el = driver.find_element(By.NAME, name)
            driver.execute_script("""
                const el = arguments[0];
                const value = arguments[1];
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(el, value);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            """, el, str(value))
            if info:
                print(f"  {name} = {value}")

        safe_fill("lat", f"{lat_val:.6f}")
        safe_fill("lon", f"{lon_val:.6f}")
        safe_fill("start", f"{min_alt_val:.1f}")
        safe_fill("stop", f"{max_alt_val:.1f}")
        safe_fill("step", f"{step_alt_val:.1f}")
        safe_fill("datetime", date_time.strftime("%Y-%m-%dT%H:%M:%S"))

        # モデルバージョン
        try:
            version_candidates = [
                f"//input[@type='radio' and (contains(@value, '{model_version.replace('IRI', '').strip()}') or contains(@value, '{model_version.strip()}'))]"
            ]
            for xpath in version_candidates:
                els = driver.find_elements(By.XPATH, xpath)
                if els:
                    driver.execute_script("arguments[0].click();", els[0])
                    if info:
                        print(f"  model = {model_version}")
                    break
            else:
                if info: print("  [warn] Error in selecting model")
        except Exception as e:
            if info: print(f"  [warn] Error in selecting model: {e}")

        # 時間・座標タイプ
        try:
            Select(driver.find_element(By.NAME, "timeType")).select_by_visible_text("Coordinated Universal Time (UTC)")
            if info: print("  time type = UTC")
            
            coord_text_map = {"geom": "Geomagnetic", "geog": "Geographic", "geomagnetic": "Geomagnetic", "geographic": "Geographic"}
            coord_text = coord_text_map.get(coord_type.lower(), "Geomagnetic")
            Select(driver.find_element(By.NAME, "coordinateType")).select_by_visible_text(coord_text)
            if info: print(f"  coordinate = {coord_text}")
        except Exception:
            if info: print("  [warn] Error in selecting time or coordinate types")

        # Reactエラーチェック
        for _ in range(10):
            if not driver.find_elements(By.CLASS_NAME, "common_errorText__MGmlx"):
                if info: print("✔ All input parameters are valid.")
                break
            time.sleep(0.5)

        # Submit監視
        if info: print("--- Monitoring Submit Button ---")
        for i in range(30):
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            if not btn.get_attribute("disabled"):
                if info: print(f"✔ Submit button enabled (after {i}s). Clicking...")
                btn.click()
                break
            time.sleep(1)
        else:
            if info: print("  [warn] Submit button timeout. Forcing submit via JS.")
            driver.execute_script("document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true }));")

        # 結果ページ待機
        if info: print("✔ Form submitted. Waiting for results...")
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Raw Output') or contains(., 'Results') or contains(., 'Download')]"))
        )

        # Resultsタブ表示
        driver.execute_script("""
            const resultsTab = Array.from(document.querySelectorAll('button, a'))
                .find(e => e.textContent.match(/Results|Output/i));
            if (resultsTab) resultsTab.click();
        """)
        time.sleep(2)

        # BeautifulSoup で解析
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")

        # 保存判定
        link = soup.find("a", string=re.compile("Raw Output|Download", re.I))
        if link and link.get("href"):
            data_url = urljoin(IRI_BASE_URL, link["href"])
            if info: print(f"Downloading data from: {data_url}")
            r = requests.get(data_url, timeout=60)
            r.raise_for_status()
            with open(output_filename, "w", encoding="utf-8") as f:
                f.write(r.text)
            if info: print(f"✔ Success: Saved to '{output_filename}'")
            return 0
        else:
            pre = soup.find("pre")
            if pre and len(pre.text.strip()) > 100:
                with open(output_filename, "w", encoding="utf-8") as f:
                    f.write(pre.text)
                if info: print(f"✔ Success: Saved to '{output_filename}' (via pre tag)")
                return 0
            else:
                if info: print("✘ Error: Result content not found.")
                return 1

    except Exception as e:
        if info: print(f"✘ Exception in _run_iri_profile_selenium: {e}")
        return 1
    finally:
        if driver:
            driver.quit()

def run_iri_profile(
        date_time: datetime,
        longitude: float,
        latitude: float,
        min_alt: float = 0,
        max_alt: float = 2000.0,
        step_alt: float = 50.0,
        model_version: str = "IRI 2020",
        output_filename: str = "iri_profile_output.txt",
        timeout: float = 30.0,
        time_type="UTC",
        coord_type="Geographic", 
        info=True,
        max_retries: int = 3
) -> int:
    """
    IRIモデルを実行し、失敗した場合は指定回数リトライする。
    成功なら0、最大リトライ後も失敗なら1を返す。
    """
    for attempt in range(max_retries):
        if info and attempt > 0:
            print(f"\n{'='*20}")
            print(f"RETRY ATTEMPT: {attempt + 1} / {max_retries}")
            print(f"{'='*20}")
        
        status = _run_iri_profile_selenium(
            date_time=date_time,
            longitude=longitude,
            latitude=latitude,
            min_alt=min_alt,
            max_alt=max_alt,
            step_alt=step_alt,
            model_version=model_version,
            output_filename=output_filename,
            timeout=timeout*2,
            time_type=time_type,
            coord_type=coord_type,
            info=info
        )
        
        if status == 0:
            return 0
        
        if attempt < max_retries - 1:
            wait_time = 5 * (attempt + 1)
            if info: print(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
            
    if info:
        print(f"\n[FATAL] Failed to retrieve IRI profile after {max_retries} attempts.")
    return 1


# ------ 2026.01.15 --------------
# def _run_iri_profile_selenium(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float,
#     max_alt: float,
#     step_alt: float,
#     model_version: str,
#     output_filename: str,
#     timeout: float = 60.0,
#     time_type="UTC",
#     coord_type="Geomagnetic",
#     info=True,
# ) -> str:
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"
#     print("--- Downloading IRI model ---")

#     # 座標補正
#     if latitude < -90: latitude = -90
#     if latitude > 90: latitude = 90
#     if longitude < 0: longitude += 360
#     if longitude > 360: longitude = longitude % 360

#     # Chrome 起動設定
#     opts = Options()
#     opts.add_argument("--headless=new")
#     opts.add_argument("--no-sandbox")
#     opts.add_argument("--disable-gpu")
#     opts.add_argument("--window-size=1920,1080")
#     opts.add_argument("user-agent=Mozilla/5.0")

#     service = Service()
#     driver = webdriver.Chrome(service=service, options=opts)
#     wait = WebDriverWait(driver, 40)
#     driver.set_page_load_timeout(timeout)
#     driver.get(IRI_BASE_URL)

#     # --- 各項目を入力 ---
#     print("--- inputing ---")
#     # --- 値を安全な範囲に補正 ---
#     latitude = max(-89.9, min(89.9, latitude))
#     longitude = longitude % 360
#     min_alt = max(0, min(2000, min_alt))
#     max_alt = max(0, min(2000, max_alt))
#     step_alt = max(1, min(500, step_alt))

#     # --- 値入力 ---
#     def safe_fill(name, value):
#         """React制御下inputへの確実な値設定"""
#         try:
#             el = driver.find_element(By.NAME, name)
#             driver.execute_script("""
#                 const el = arguments[0];
#                 const value = arguments[1];
#                 const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
#                 nativeInputValueSetter.call(el, value);
#                 el.dispatchEvent(new Event('input', { bubbles: true }));
#                 el.dispatchEvent(new Event('change', { bubbles: true }));
#             """, el, str(value))
#             print(f"  {name} = {value}")
#         except Exception as e:
#             print(f"  [warn] {name} Error: {e}")

#     safe_fill("lat", f"{latitude:.6f}")
#     safe_fill("lon", f"{longitude:.6f}")
#     # safe_fill("height", f"{min_alt:.1f}")
#     safe_fill("start", f"{min_alt:.1f}")
#     safe_fill("stop", f"{max_alt:.1f}")
#     safe_fill("step", f"{step_alt:.1f}")
#     safe_fill("datetime", date_time.strftime("%Y-%m-%dT%H:%M:%S"))
#     # モデルバージョン
#     try:
#         # "2020" や "IRI 2020" の両方にマッチ
#         version_candidates = [
#             f"//input[@type='radio' and (contains(@value, '{model_version.replace('IRI', '').strip()}') or contains(@value, '{model_version.strip()}'))]"
#         ]
#         for xpath in version_candidates:
#             els = driver.find_elements(By.XPATH, xpath)
#             if els:
#                 driver.execute_script("arguments[0].click();", els[0])
#                 if info:
#                     print(f"  model = {model_version}")
#                 break
#         else:
#             print("  [warn] Error in selecting model")
#     except Exception as e:
#         print(f"  [warn] Error in selecting model: {e}")

#     # 時間タイプ選択
#     try:
#         Select(driver.find_element(By.NAME, "timeType")).select_by_visible_text("Coordinated Universal Time (UTC)")
#         print("  time type = UTC")
#     except Exception:
#         print("  [warn] Error in selecting time type")

#     # 座標タイプ
#     try:
#         coord_text_map = {
#             "geom": "Geomagnetic",
#             "geog": "Geographic",
#             "geomagnetic": "Geomagnetic",
#             "geographic": "Geographic",
#         }
#         coord_text = coord_text_map.get(coord_type.lower(), "Geomagnetic")  # デフォルトはGeomagnetic
#         Select(driver.find_element(By.NAME, "coordinateType")).select_by_visible_text(coord_text)
#         print(f"  coordinate = {coord_text}")
#     except Exception:
#         print("  [warn] Error in selecting coordinate")
        
#     # Reactのエラーメッセージが消えるのを確認
#     for _ in range(10):
#         if not driver.find_elements(By.CLASS_NAME, "common_errorText__MGmlx"):
#             if info:
#                 print("✔ All the input parameters are valid.")
#             break
#         time.sleep(0.5)
#     else:
#         print("  [warn] Reactエラーメッセージが残っています。送信を試みます。")

#     # --- Submitボタン監視とReact対応送信 ---
#     if info:
#         print("--- Submitボタンの有効化を監視中 ---")
#     # React側でdisabled解除されるまで明示的にチェックループ
#     for i in range(30):
#         btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
#         disabled = btn.get_attribute("disabled")
#         if not disabled:
#             if info:
#                 print(f"✔ Detected the submit button ({i}) s")
#             btn.click()
#             break
#         time.sleep(1)
#     else:
#         print("  [warn] Submitボタンが有効化されませんでした。JS経由で強制送信。")
#         driver.execute_script("""
#             const form = document.querySelector('form');
#             if (form) {
#                 const evt = new Event('submit', { bubbles: true, cancelable: true });
#                 form.dispatchEvent(evt);
#             }
#         """)

#     if info:
#         print("✔ フォーム送信完了。結果ページを待機中...")

#     # --- 結果ページ（React生成）を待機 ---
#     try:
#         # "Results" or "Raw Output" が現れるまで最大60秒待機
#         if info:
#             print("--- 結果ページ読み込みを待機中（最大60秒）---")
#         WebDriverWait(driver, 60).until(
#             EC.presence_of_element_located(
#                 (By.XPATH, "//a[contains(., 'Raw Output') or contains(., 'Results') or contains(., 'Download')]")
#             )
#         )
#     except Exception:
#         print("  [warn] 結果リンクが60秒以内に現れませんでした。ページ全体を保存します。")

#     # ReactのResultsタブを明示的に開く（Raw Outputがhidden状態のことがあるため）
#     try:
#         driver.execute_script("""
#             const resultsTab = Array.from(document.querySelectorAll('button, a'))
#                 .find(e => e.textContent.match(/Results|Output/i));
#             if (resultsTab) resultsTab.click();
#         """)
#         time.sleep(2)
#     except Exception:
#         print("  [warn] Resultsタブのクリックに失敗しました。")

#     # BeautifulSoup でページ再取得
#     page_source = driver.page_source
#     soup = BeautifulSoup(page_source, "html.parser")

#     # --- Raw Outputリンクまたは<pre>を検出 ---
#     link = soup.find("a", string=re.compile("Raw Output|Download", re.I))
#     if link and link.get("href"):
#         data_url = urljoin(IRI_BASE_URL, link["href"])
#         print(f"Downloading: {data_url}")
#         r = requests.get(data_url, timeout=60)
#         r.raise_for_status()
#         with open(output_filename, "w", encoding="utf-8") as f:
#             f.write(r.text)
#         result = f"Saved: '{output_filename}'"
#     else:
#         pre = soup.find("pre")
#         if pre and len(pre.text.strip()) > 100:
#             with open(output_filename, "w", encoding="utf-8") as f:
#                 f.write(pre.text)
#             result = f"成功: '{output_filename}' に保存しました（preタグ経由）"
#         else:
#             with open(output_filename, "w", encoding="utf-8") as f:
#                 f.write(page_source)
#             result = f"警告: 結果テキストを検出できず、HTMLを保存しました。"

#     driver.quit()
#     print(result)
#     return result

# # --- メインデータ取得関数 (requests/BeautifulSoup) ---

# def run_iri_profile(
#        date_time: datetime,
#         longitude: float,
#         latitude: float,
#         min_alt: float = 0,
#         max_alt: float = 2000.0,
#         step_alt: float = 50.0,
#         model_version: str = "IRI 2020",
#         output_filename: str = "iri_profile_output.txt",
#         timeout: float = 30.0,
#         time_type="UTC",
#         coord_type="Geographic", 
#         info=True
# ):
#     return _run_iri_profile_selenium(
#         date_time=date_time,
#         longitude=longitude,
#         latitude=latitude,
#         min_alt=min_alt,
#         max_alt=max_alt,
#         step_alt=step_alt,
#         model_version=model_version,
#         output_filename=output_filename,
#         timeout=timeout*2,
#         time_type=time_type,
#         coord_type=coord_type,
#         info=info
#     )

# ------ 2026.01.15 --------------


# def _translate_time_type(v):
#     if v is None:
#         return ''
#     vs = str(v).strip().lower()
#     map_tt = {
#         'utc': '0',
#         'coordinate universal time (utc)': '0',
#         'universal': '0',
#         'local': '1',
#         'lt': '1',
#     }
#     if vs in map_tt:
#         return map_tt[vs]
#     # if user passed number-like, return as-is
#     if vs.isdigit():
#         return vs
#     return vs

# def _translate_coord_type(v):
#     if v is None:
#         return ''
#     vs = str(v).strip().lower()
#     map_ct = {
#         'geographic': '0',
#         'geodetic': '0',
#         'geocentric': '0',
#         'geomagnetic': '1',
#         'magnetic': '1',
#     }
#     if vs in map_ct:
#         return map_ct[vs]
#     if vs.isdigit():
#         return vs
#     return vs


# def _verify_iri_parameters(
#     soup: BeautifulSoup,
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     model_version: str,
#     time_type: str,
#     coord_type: str,
# ) -> Dict[str, Any]:
#     """
#     IRI結果ページから入力パラメータのサマリーをパースし、期待値と比較します。

#     Args:
#         soup: 結果ページのBeautifulSoupオブジェクト。
#         ... (その他の引数は期待される入力パラメータ)

#     Returns:
#         dict: 検証結果 ("success": bool, "log": str)
#     """
#     log_messages = ["--- [検証] パラメータ検証開始 ---"]
#     success = True
#     page_text = soup.get_text()

#     # A. 緯度/経度/高度の検証 (より柔軟な正規表現を使用)
#     # 例: geog Lat/Long/Alt= 10.0/ 110.0/ 300.0
#     # geog/geomagの有無を許容し、緯度(2)、経度(3)、高度(4)をキャプチャ
#     # DOTALLフラグにより、改行をまたいでもマッチできるように改善
#     match_coord = re.search(
#         r'(geog|geomag)?\s*Lat/Long/Alt=\s*([\d\.\-]+)/\s*([\d\.\-]+)/\s*([\d\.\-]+)', 
#         page_text,
#         re.IGNORECASE | re.DOTALL
#     )
    
#     if match_coord:
#         try:
#             # グループ2が緯度、グループ3が経度
#             page_lat = float(match_coord.group(2))
#             page_lon = float(match_coord.group(3))
            
#             # 許容誤差 0.1度
#             if abs(page_lat - latitude) > 0.1 or abs(page_lon - longitude) > 0.1:
#                 log_messages.append(f"検証失敗: 緯度/経度不一致。期待: {latitude:.1f}/{longitude:.1f}、ページ: {page_lat:.1f}/{page_lon:.1f}")
#                 success = False
#             else:
#                 log_messages.append(f"検証成功: 緯度/経度 ({latitude:.1f}/{longitude:.1f}) は正確に反映されています。")
#         except ValueError:
#              log_messages.append("警告: 緯度/経度の値が数値としてパースできませんでした。")
#     else:
#         log_messages.append("警告: 緯度/経度/高度のサマリー行が見つかりません。")

#     # B. 日付/時刻の検証 (より柔軟な正規表現を使用)
#     # 例: yyyy/mmdd(or -ddd)/hh.h):2012/ -42/10.0UT
#     # 年(1)と時刻(2)をキャプチャ。日番号の部分は[^\s/]*でスキップ。
#     match_dt_full = re.search(
#         r'yyyy/mmdd\(or\s*-ddd\)/hh\.h\):\s*(\d{4})/[^\s/]*\/([\d\.]+)(UT|LT)', 
#         page_text, 
#         re.IGNORECASE | re.MULTILINE | re.DOTALL
#     )

#     if match_dt_full:
#         page_year = int(match_dt_full.group(1))
        
#         # ページ上の時刻 (例: 10.0) を抽出
#         page_hour_float = float(match_dt_full.group(2))
        
#         if page_year != date_time.year:
#             log_messages.append(f"検証失敗: 年不一致。期待: {date_time.year}、ページ: {page_year}")
#             success = False
        
#         try:
#             # 期待される時刻 (float)
#             expected_hour_float = date_time.hour + date_time.minute / 60.0 + date_time.second / 3600.0
            
#             # 許容誤差 0.1時間 (6分)
#             if abs(page_hour_float - expected_hour_float) > 0.1:
#                 log_messages.append(f"検証失敗: 時刻不一致。期待: {expected_hour_float:.1f}h、ページ: {page_hour_float:.1f}h")
#                 success = False
#             else:
#                 log_messages.append(f"検証成功: 日付/時刻 ({date_time.year}, {expected_hour_float:.1f}h) は正確に反映されています。")
#         except ValueError:
#             log_messages.append("警告: 時刻（hh.h）の値が数値としてパースできませんでした。")
#     else:
#         log_messages.append("警告: 日付/時刻のヘッダー行が見つかりません。")

#     # C. モデルバージョンの検証
#     # 例: IRIcor2 is used for topside Ne profile / URSI maps are used...
#     expected_model_str = model_version.replace('-', '') # IRI2020 -> IRI2020
    
#     # ページ内のテキストに model_version が含まれているか（大文字・小文字を無視して）
#     if expected_model_str.lower() in page_text.lower():
#         log_messages.append(f"検証成功: モデルバージョン '{model_version}' が確認されました。")
#     else:
#         # このチェックは厳密ではないため、失敗しても警告に留めます
#         log_messages.append(f"警告: モデルバージョン '{model_version}' の確認ができませんでした。")

#     # D. 座標タイプの検証 (Geog/Geomagneticのキーワード)
    
#     ct_value = _translate_coord_type(coord_type)
    
#     if ct_value == '0': # Geographic/Geodetic/Geocentric
#         expected_coord_keyword = "geog"
#     elif ct_value == '1': # Geomagnetic/Magnetic
#         expected_coord_keyword = "geomagnetic"
#     else:
#         expected_coord_keyword = None

#     if expected_coord_keyword:
#         if expected_coord_keyword in page_text.lower():
#             log_messages.append(f"検証成功: 座標タイプ '{coord_type}' ({expected_coord_keyword}キーワード) が確認されました。")
#         else:
#             log_messages.append(f"検証失敗: 座標タイプ '{coord_type}' のキーワードがページに見つかりません。")
#             success = False # 座標タイプは重要な設定なので失敗とします

#     log_messages.append("--- [検証] パラメータ検証終了 ---")

#     return {
#         "success": success,
#         "log": "\n".join(log_messages)
#     }





# def old_run_iri_profile(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float = 80.0,
#     max_alt: float = 2000.0,
#     step_alt: float = 50.0,
#     model_version: str = "IRI 2020",
#     output_filename: str = "iri_profile_output.txt",
#     timeout: float = 30.0,
#     time_type="UTC",
#     coord_type="Geographic",
# ) -> str:
#     """
#     CCMC IRI Instant Run サービスを使用して、指定されたパラメータで
#     高度プロファイルデータを取得し、ファイルに保存します。

#     非ブラウザでのフォーム送信を試み、失敗した場合は Selenium にフォールバックします。
#     """
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"
#     print(f"--- IRI Profile Request ---")
#     print(f"日時: {date_time.isoformat()}, 緯度/経度: {latitude}/{longitude}")
#     print(f"高度範囲: {min_alt}-{max_alt}km, ステップ: {step_alt}km")

#     session = requests.Session()
#     session.headers.update({
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
#         'Referer': IRI_BASE_URL,
#     })

#     try:
#         # 1. 初期ページを取得し、フォームの隠しフィールドとアクションを抽出
#         r0 = session.get(IRI_BASE_URL, timeout=timeout)
#         r0.raise_for_status()
#     except requests.RequestException as e:
#         return f"エラー（初回GET）: サイトへの接続に失敗しました: {e}"

#     soup0 = BeautifulSoup(r0.content, "html.parser")
#     form = soup0.find("form")
#     submit_url = IRI_BASE_URL
#     form_method = "post"
#     raw_action = ""
    
#     if form:
#         if form.get("action"):
#             submit_url = urljoin(IRI_BASE_URL, form.get("action"))
#             raw_action = form.get("action") or ""
#         form_method = (form.get("method") or "post").lower()

#     # 隠し入力フィールドを抽出
#     hidden_inputs = {}
#     for inp in soup0.find_all("input"):
#         n = inp.get("name")
#         t = inp.get("type", "").lower()
#         v = inp.get("value", "")
#         if n and (t == "hidden" or t == "submit" or t == "button"):
#             hidden_inputs[n] = v

#     # 2. ペイロードを準備
#     payload = {
#         'Year': str(date_time.year),
#         'Month': str(date_time.month),
#         'Day': str(date_time.day),
#         'Hour': str(date_time.hour),
#         'Minute': str(date_time.minute),
#         'Second': str(date_time.second),
#         'ut_type': _translate_time_type(time_type),
#         'Longitude': f"{longitude:.3f}",
#         'Latitude': f"{latitude:.3f}",
#         'coord_type': _translate_coord_type(coord_type),
#         'min_alt': f"{min_alt:.1f}",
#         'max_alt': f"{max_alt:.1f}",
#         'step_alt': f"{step_alt:.1f}",
#         'alt_type': '0',   # 0: Altitude Profile
#         'grid_type': '0',  # 0: Standard Profile (Altitude)
#         'version': model_version, # 'version' キーが使われることを想定
#         'submit_button': 'Submit',
#     }
#     # 隠しフィールドをマージ (既存のキーを上書きしないように注意)
#     final_payload = {**hidden_inputs, **payload}

#     # out_vars (出力変数) の設定
#     out_vars_values = []
#     if form:
#         for inp in form.find_all(["input", "select"]):
#             name = inp.get("name", "")
#             if "out" in name.lower() and "var" in name.lower():
#                 if inp.name == "input" and inp.get("value"):
#                     out_vars_values.append(inp.get("value"))
#                 elif inp.name == "select":
#                     for opt in inp.find_all("option"):
#                         if opt.get("value"):
#                             out_vars_values.append(opt.get("value"))
#     if not out_vars_values:
#         out_vars_values = ['1','2','3','4','5','6','7','8','9','10']

#     # data_list の作成 (複数キーを考慮)
#     data_list = []
#     for k, v in final_payload.items():
#         data_list.append((k, str(v)))
#     # out_vars は複数回送信する必要がある
#     for v in out_vars_values:
#         data_list.append(("out_vars", str(v)))

#     # 3. フォームアクションのチェックとフォールバック
#     if form is not None:
#         raw = raw_action.strip().lower()
#         if raw in ("", "#") or raw.startswith("javascript"):
#             print("警告: フォームアクションがJSベースのため、Seleniumにフォールバックします。")
#             return _run_iri_profile_selenium(
#                 date_time=date_time,
#                 longitude=longitude,
#                 latitude=latitude,
#                 min_alt=min_alt,
#                 max_alt=max_alt,
#                 step_alt=step_alt,
#                 model_version=model_version,
#                 output_filename=output_filename,
#                 timeout=timeout*2,
#                 time_type=time_type,  # Seleniumに渡す引数を追加
#                 coord_type=coord_type # Seleniumに渡す引数を追加
#             )

#     # 4. フォーム送信
#     try:
#         session.headers.update({'Referer': IRI_BASE_URL})
#         if form_method == "get":
#             r1 = session.get(submit_url, params=data_list, allow_redirects=True, timeout=timeout)
#         else:
#             r1 = session.post(submit_url, data=data_list, allow_redirects=True, timeout=timeout)
#         r1.raise_for_status()
#         print("非ブラウザ送信: 成功。結果ページを解析中...")
#     except requests.RequestException as e:
#         print(f"警告: 非ブラウザ送信に失敗 ({type(e).__name__} - {e})。Seleniumにフォールバックします。")
#         return _run_iri_profile_selenium(
#             date_time=date_time,
#             longitude=longitude,
#             latitude=latitude,
#             min_alt=min_alt,
#             max_alt=max_alt,
#             step_alt=step_alt,
#             model_version=model_version,
#             output_filename=output_filename,
#             timeout=timeout*2,
#             time_type=time_type,  # Seleniumに渡す引数を追加
#             coord_type=coord_type # Seleniumに渡す引数を追加
#         )

#     # 5. 結果ページからダウンロードリンクまたは生テキストを抽出
#     final_url = r1.url
#     soup1 = BeautifulSoup(r1.content, "html.parser")

#     # --- [追加されたパラメータ検証] ---
#     verification_result = _verify_iri_parameters(
#         soup1, date_time, longitude, latitude, model_version, time_type, coord_type
#     )
#     # ここで検証ログが出力されます（非ブラウザ送信成功時）
#     print(verification_result["log"]) 
#     if not verification_result["success"]:
#         pass # 検証失敗してもダウンロードは試みる
#     # ------------------------------------

#     def find_download_link(soup):
#         tags = soup.find_all("a")
#         for a in tags:
#             href = a.get("href", "")
#             txt = (a.get_text() or "").strip().lower()
#             if not href:
#                 continue
#             if "download" in txt or "view raw" in txt or "raw output" in txt:
#                 return href
#             if href.endswith((".txt", ".out")) or "/data/" in href or "output" in href.lower():
#                 return href
#         return None

#     href = find_download_link(soup1)
#     data_url = None
#     if href:
#         data_url = urljoin(final_url, href)

#     if not data_url:
#         # ダウンロードリンクがない場合、<pre>タグ内の生テキストを探す
#         pre = soup1.find("pre")
#         if pre:
#             try:
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(pre.get_text())
#                 return f"成功: ファイル '{output_filename}' を保存しました (非ブラウザ/preタグ)"
#             except Exception as e:
#                 return f"エラー（ファイル書き込み）: {e}"
        
#         return "エラー: 結果ページの解析に失敗しました。ダウンロードリンクまたは生テキストが見つかりません。"

#     # 6. データダウンロード
#     print(f"非ブラウザ: ダウンロードURLを検出: {data_url}")
#     try:
#         r2 = session.get(data_url, timeout=timeout)
#         r2.raise_for_status()
#         with open(output_filename, "w", encoding="utf-8") as f:
#             f.write(r2.text)
#         return f"成功: ファイル '{output_filename}' を保存しました (非ブラウザ経由)"
#     except requests.RequestException as e:
#         return f"エラー（データダウンロード）: {e}"
#     except IOError as e:
#         return f"エラー（ファイル書き込み）: {e}"


# def old_run_iri_profile_selenium(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float,
#     max_alt: float,
#     step_alt: float,
#     model_version: str,
#     output_filename: str,
#     timeout: float = 60.0,
#     time_type="UTC",
#     coord_type="Geomagnetic",
# ) -> str:
#     """
#     Selenium を使ってブラウザ経由でフォームを送信し、生出力（raw text）を取得する。
#     必要パッケージ: selenium, webdriver-manager
#       pip install selenium webdriver-manager
#     """
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"

#     try:
#         from selenium import webdriver
#         from selenium.webdriver.common.by import By
#         from selenium.webdriver.support.ui import WebDriverWait
#         from selenium.webdriver.support import expected_conditions as EC
#         from selenium.webdriver.chrome.service import Service
#         from webdriver_manager.chrome import ChromeDriverManager
#         from selenium.webdriver.chrome.options import Options
#     except Exception as e:
#         return ("Error: Selenium 関連モジュールが見つかりません。"
#                 " 'pip install selenium webdriver-manager' を実行してから再度試してください.\n"
#                 f"詳細: {e}")

#     opts = Options()
#     # headlessモード（必要に応じて無効化して手動確認）
#     opts.add_argument("--headless=new")
#     opts.add_argument("--disable-gpu")
#     opts.add_argument("--no-sandbox")
#     opts.add_argument("--window-size=1920,1080")
#     opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")

#     service = Service(ChromeDriverManager().install())
#     driver = webdriver.Chrome(service=service, options=opts)

#     try:
#         driver.set_page_load_timeout(timeout)
#         driver.get(IRI_BASE_URL)

#         wait = WebDriverWait(driver, 30)

#         # フォームが読み込まれるまで待つ
#         wait.until(EC.presence_of_element_located((By.TAG_NAME, "form")))

#         # 入力要素を可能な限り埋める（存在する名前に合わせる）
#         def safe_fill(name, value):
#             try:
#                 el = driver.find_element(By.NAME, name)
#                 driver.execute_script("arguments[0].value = arguments[1];", el, value)
#                 return True
#             except Exception:
#                 return False

#         # フォームのフィールド名はページにより変わるため複数候補で補完
#         # datetime
#         dt_str = date_time.strftime("%Y-%m-%d %H:%M:%S")
#         for n in ("datetime", "DateTime", "date_time"):
#             if safe_fill(n, dt_str):
#                 break

#         # lat/lon
#         for n in ("lat", "Latitude", "latitude", 'Latitude (-90° to 90°)'):
#             safe_fill(n, f"{latitude:.6f}")
#         for n in ("lon", "Longitude", "longitude"):
#             safe_fill(n, f"{longitude:.6f}")

#         # altitude grid fields (ページ内のフィールド名に合わせる)
#         for n in ("start", "min_alt", "minAlt", "height_start"):
#             safe_fill(n, str(min_alt))
#         for n in ("stop", "max_alt", "maxAlt", "height_stop"):
#             safe_fill(n, str(max_alt))
#         for n in ("step", "step_alt", "stepAlt"):
#             safe_fill(n, str(step_alt))

#         # version / model
#         for n in ("version", "model_version", "versionSelect"):
#             safe_fill(n, model_version)

#         # --- time type / coordinate type ---
#         # try to set select/input values for time type (names vary)
#         tt_value = str(_translate_time_type(time_type))
#         for n in ("ut_type", "utType", "time_type", "timeType"):
#             try:
#                 # try select first
#                 from selenium.webdriver.support.ui import Select
#                 sel = Select(driver.find_element(By.NAME, n))
#                 sel.select_by_value(tt_value)
#                 break
#             except Exception:
#                 safe_fill(n, tt_value)

#         ct_value = str(_translate_coord_type(coord_type))
#         for n in ("coord_type", "coordType", "coordinate_type", "coord"):
#             try:
#                 from selenium.webdriver.support.ui import Select
#                 sel = Select(driver.find_element(By.NAME, n))
#                 sel.select_by_value(ct_value)
#                 break
#             except Exception:
#                 safe_fill(n, ct_value)
#         # checkboxes/selects for optional outputs: try to enable reasonable defaults
#         try:
#             # 例: useOptionals がある場合
#             elems = driver.find_elements(By.NAME, "useOptionals")
#             for e in elems:
#                 try:
#                     driver.execute_script("arguments[0].checked = true;", e)
#                 except Exception:
#                     pass
#         except Exception:
#             pass

#         # Submit ボタンを探してクリック
#         clicked = False
#         try:
#             # input[type=submit] / button[text() contains Submit]
#             submits = driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button' or @type='image']")
#             for s in submits:
#                 try:
#                     val = s.get_attribute("value") or s.text or ""
#                     if "Submit" in val or "Run" in val or "Calculate" in val or val.strip() == "":
#                         s.click()
#                         clicked = True
#                         break
#                 except Exception:
#                     continue
#         except Exception:
#             pass

#         if not clicked:
#             try:
#                 btn = driver.find_element(By.XPATH, "//button[contains(., 'Submit') or contains(., 'Run') or contains(., 'Calculate')]")
#                 btn.click()
#                 clicked = True
#             except Exception:
#                 pass

#         if not clicked:
#             return "エラー: Submit ボタンが見つかりませんでした（Selenium）"

#         # 結果ページ/リンクが現れるまで待つ（最大 timeout 秒）
#         # 「View Raw Output」や「Download」が現れるまで待つ
#         try:
#             # 優先: View Raw Output / Download のリンク
#             link = WebDriverWait(driver, 40).until(
#                 EC.presence_of_element_located((
#                     By.XPATH,
#                     "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'view raw') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'download') or contains(., 'Raw Output')]"
#                 ))
#             )
#             href = link.get_attribute("href")
#         except Exception:
#             # 代替: results.php 等に遷移しているか pre タグ に出力があるか
#             href = None

#         # cookies を requests セッションに移してダウンロードを試みる
#         sess = requests.Session()
#         for c in driver.get_cookies():
#             sess.cookies.set(c['name'], c['value'], domain=c.get('domain', None))

#         if href:
#             data_url = href if href.startswith("http") else urljoin(driver.current_url, href)
#             try:
#                 r = sess.get(data_url, timeout=60)
#                 r.raise_for_status()
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(r.text)
#                 return output_filename
#             except Exception as e:
#                 # フォールバックして、リンクをクリックして表示されたページのテキストを保存
#                 try:
#                     link.click()
#                     time.sleep(1)
#                     txt = driver.page_source
#                     with open(output_filename, "w", encoding="utf-8") as f:
#                         f.write(txt)
#                     return output_filename
#                 except Exception as e2:
#                     return f"エラー（Selenium ダウンロード）: {e} / fallback: {e2}"

#         # href が無い場合はページ内の <pre> を探し生テキストを保存
#         try:
#             pre = driver.find_element(By.TAG_NAME, "pre")
#             txt = pre.text
#             with open(output_filename, "w", encoding="utf-8") as f:
#                 f.write(txt)
#             return output_filename
#         except Exception:
#             # 最終手段: ページ全体のテキストを保存
#             try:
#                 txt = driver.page_source
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(txt)
#                 return output_filename
#             except Exception as e:
#                 return f"エラー（Selenium 最終保存失敗）: {e}"
#     finally:
#         try:
#             driver.quit()
#         except Exception:
#             pass


# def _run_iri_profile(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float = 80.0,
#     max_alt: float = 2000.0,
#     step_alt: float = 50.0,
#     model_version: str = "IRI-2020",
#     output_filename: str = "iri_profile_output.txt",
#     timeout: float = 30.0,
#     time_type="UTC",
#     coord_type="Geomagnetic",
# ) -> str:
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"

#     session = requests.Session()
#     session.headers.update({
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
#         'Referer': IRI_BASE_URL,
#     })

#     try:
#         r0 = session.get(IRI_BASE_URL, timeout=timeout)
#         r0.raise_for_status()
#     except requests.RequestException as e:
#         return f"Error (session get): {e}"

#     soup0 = BeautifulSoup(r0.content, "html.parser")
#     form = soup0.find("form")
#     submit_url = IRI_BASE_URL
#     form_method = "post"
#     raw_action = ""
#     if form:
#         if form.get("action"):
#             submit_url = urljoin(IRI_BASE_URL, form.get("action"))
#             raw_action = form.get("action") or ""
#         form_method = (form.get("method") or "post").lower()

#     # debug: show discovered form info
#     # print(f"[DEBUG] detected form action -> {submit_url}")
#     # print(f"[DEBUG] detected form method -> {form_method}")

#     hidden_inputs = {}
#     for inp in soup0.find_all("input"):
#         n = inp.get("name")
#         t = inp.get("type", "").lower()
#         v = inp.get("value", "")
#         if not n:
#             continue
#         hidden_inputs[n] = v
#     # print(f"[DEBUG] discovered form fields (sample): {list(hidden_inputs.keys())[:30]}")


#     # prepare payload and include translated fields
#     payload = {
#         'Year': str(date_time.year),
#         'Month': str(date_time.month),
#         'Day': str(date_time.day),
#         'Hour': str(date_time.hour),
#         'Minute': str(date_time.minute),
#         'Second': str(date_time.second),
#         'ut_type': _translate_time_type(time_type),
#         'Longitude': f"{longitude:.3f}",
#         'Latitude': f"{latitude:.3f}",
#         'coord_type': _translate_coord_type(coord_type),
#         'coord_type': _translate_coord_type(coord_type),
#         'coord_type': _translate_coord_type(coord_type),
#         # ... rest unchanged ...
#     }
#     # make sure keys used by site are present; if site expects different names, Selenium fallback will handle
#     payload.update(hidden_inputs)

#     # collect out_vars if present in the form
#     out_vars_values = []
#     if form:
#         for inp in form.find_all(["input", "select"]):
#             name = inp.get("name", "")
#             if not name:
#                 continue
#             if "out" in name and "var" in name:
#                 if inp.name == "input":
#                     v = inp.get("value")
#                     if v:
#                         out_vars_values.append(v)
#                 elif inp.name == "select":
#                     for opt in inp.find_all("option"):
#                         if opt.get("value"):
#                             out_vars_values.append(opt.get("value"))
#     if not out_vars_values:
#         out_vars_values = ['1','2','3','4','5','6','7','8','9','10']

#     # prepare params/data list (repeated keys allowed)
#     data_list = []
#     for k, v in payload.items():
#         data_list.append((k, str(v)))
#     for v in out_vars_values:
#         data_list.append(("out_vars", str(v)))

#     # print(f"[DEBUG] prepared {len(data_list)} form items (showing first 20): {data_list[:20]}")

#     # If the form's action is empty or a JS anchor, switch to Selenium fallback
#     if form is not None:
#         raw = raw_action.strip().lower()
#         if raw in ("", "#") or raw.startswith("javascript"):
#             # print("[DEBUG] form action is JS-only -> switching to Selenium")
#             return old_run_iri_profile_selenium(
#                 date_time=date_time,
#                 longitude=longitude,
#                 latitude=latitude,
#                 min_alt=min_alt,
#                 max_alt=max_alt,
#                 step_alt=step_alt,
#                 model_version=model_version,
#                 output_filename=output_filename,
#                 timeout=timeout*2
#             )

#     try:
#         session.headers.update({'Referer': IRI_BASE_URL})
#         if form_method == "get":
#             # print(f"[DEBUG] sending GET to {submit_url} with params")
#             r1 = session.get(submit_url, params=data_list, allow_redirects=True, timeout=timeout)
#         else:
#             # print(f"[DEBUG] sending POST to {submit_url}")
#             r1 = session.post(submit_url, data=data_list, allow_redirects=True, timeout=timeout)
#         r1.raise_for_status()
#     except requests.RequestException as e:
#         msg = f"エラー（送信）: {e}"
#         try:
#             resp = e.response
#             if resp is not None:
#                 msg += f" (status={resp.status_code}, url={resp.url})\nResponse headers: {resp.headers}\nResponse text head: {resp.text[:800]}"
#         except Exception:
#             pass
#         return msg

#     final_url = r1.url
#     final_html = r1.content
#     soup1 = BeautifulSoup(final_html, "html.parser")

#     def find_download_link(soup):
#         tags = soup.find_all("a")
#         for a in tags:
#             href = a.get("href", "")
#             txt = (a.get_text() or "").strip().lower()
#             if not href:
#                 continue
#             if "download" in txt or "view raw" in txt or "raw output" in txt:
#                 return href
#             if href.endswith(".txt") or href.endswith(".out") or "/data/" in href or "output" in href.lower():
#                 return href
#         return None

#     href = find_download_link(soup1)
#     data_url = None
#     if href:
#         data_url = urljoin(final_url, href)
#     else:
#         m = re.search(r"runID=([A-Za-z0-9_\-]+)", final_url)
#         runid = m.group(1) if m else None
#         if not runid:
#             txt = soup1.get_text()
#             m2 = re.search(r"runID=([A-Za-z0-9_\-]+)", txt)
#             runid = m2.group(1) if m2 else None
#         if runid:
#             candidate = urljoin(IRI_BASE_URL, f"data/output_{runid}.txt")
#             data_url = candidate

#     if not data_url:
#         pre = soup1.find("pre")
#         if pre:
#             try:
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(pre.get_text())
#                 return output_filename
#             except Exception as e:
#                 return f"エラー（pre保存）: {e}"
#         return "エラー: ダウンロードリンクが見つかりませんでした。フォームの実際の送信はブラウザ(Networkタブ)を確認してください。"

#     try:
#         r2 = session.get(data_url, timeout=timeout)
#         r2.raise_for_status()
#         with open(output_filename, "w", encoding="utf-8") as f:
#             f.write(r2.text)
#         return output_filename
#     except requests.RequestException as e:
#         return f"エラー（データダウンロード）: {e}"
#     except IOError as e:
#         return f"エラー（ファイル書き込み）: {e}"
    

# def old_run_iri_profile_selenium(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float,
#     max_alt: float,
#     step_alt: float,
#     model_version: str,
#     output_filename: str,
#     timeout: float = 60.0,
# ) -> str:
#     """
#     Selenium を使ってブラウザ経由でフォームを送信し、生出力（raw text）を取得する。
#     必要パッケージ: selenium, webdriver-manager
#       pip install selenium webdriver-manager
#     """
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"

#     try:
#         from selenium import webdriver
#         from selenium.webdriver.common.by import By
#         from selenium.webdriver.support.ui import WebDriverWait
#         from selenium.webdriver.support import expected_conditions as EC
#         from selenium.webdriver.chrome.service import Service
#         from webdriver_manager.chrome import ChromeDriverManager
#         from selenium.webdriver.chrome.options import Options
#     except Exception as e:
#         return ("エラー: Selenium 関連モジュールが見つかりません。"
#                 " 'pip install selenium webdriver-manager' を実行してから再度試してください.\n"
#                 f"詳細: {e}")

#     opts = Options()
#     # headlessモード（必要に応じて無効化して手動確認）
#     opts.add_argument("--headless=new")
#     opts.add_argument("--disable-gpu")
#     opts.add_argument("--no-sandbox")
#     opts.add_argument("--window-size=1920,1080")
#     opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")

#     service = Service(ChromeDriverManager().install())
#     driver = webdriver.Chrome(service=service, options=opts)

#     try:
#         driver.set_page_load_timeout(timeout)
#         driver.get(IRI_BASE_URL)

#         wait = WebDriverWait(driver, 30)

#         # フォームが読み込まれるまで待つ
#         wait.until(EC.presence_of_element_located((By.TAG_NAME, "form")))

#         # 入力要素を可能な限り埋める（存在する名前に合わせる）
#         def safe_fill(name, value):
#             try:
#                 el = driver.find_element(By.NAME, name)
#                 driver.execute_script("arguments[0].value = arguments[1];", el, value)
#                 return True
#             except Exception:
#                 return False

#         # フォームのフィールド名はページにより変わるため複数候補で補完
#         # datetime
#         dt_str = date_time.strftime("%Y-%m-%d %H:%M:%S")
#         for n in ("datetime", "DateTime", "date_time"):
#             if safe_fill(n, dt_str):
#                 break

#         # lat/lon
#         for n in ("lat", "Latitude", "latitude"):
#             safe_fill(n, f"{latitude:.6f}")
#         for n in ("lon", "Longitude", "longitude"):
#             safe_fill(n, f"{longitude:.6f}")

#         # altitude grid fields (ページ内のフィールド名に合わせる)
#         for n in ("start", "min_alt", "minAlt", "height_start"):
#             safe_fill(n, str(min_alt))
#         for n in ("stop", "max_alt", "maxAlt", "height_stop"):
#             safe_fill(n, str(max_alt))
#         for n in ("step", "step_alt", "stepAlt"):
#             safe_fill(n, str(step_alt))

#         # version / model
#         for n in ("version", "model_version", "versionSelect"):
#             safe_fill(n, model_version)

#         # checkboxes/selects for optional outputs: try to enable reasonable defaults
#         try:
#             # 例: useOptionals がある場合
#             elems = driver.find_elements(By.NAME, "useOptionals")
#             for e in elems:
#                 try:
#                     driver.execute_script("arguments[0].checked = true;", e)
#                 except Exception:
#                     pass
#         except Exception:
#             pass

#         # Submit ボタンを探してクリック
#         clicked = False
#         try:
#             # input[type=submit] / button[text() contains Submit]
#             submits = driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button' or @type='image']")
#             for s in submits:
#                 try:
#                     val = s.get_attribute("value") or s.text or ""
#                     if "Submit" in val or "Run" in val or "Calculate" in val or val.strip() == "":
#                         s.click()
#                         clicked = True
#                         break
#                 except Exception:
#                     continue
#         except Exception:
#             pass

#         if not clicked:
#             try:
#                 btn = driver.find_element(By.XPATH, "//button[contains(., 'Submit') or contains(., 'Run') or contains(., 'Calculate')]")
#                 btn.click()
#                 clicked = True
#             except Exception:
#                 pass

#         if not clicked:
#             return "エラー: Submit ボタンが見つかりませんでした（Selenium）"

#         # 結果ページ/リンクが現れるまで待つ（最大 timeout 秒）
#         # 「View Raw Output」や「Download」が現れるまで待つ
#         try:
#             # 優先: View Raw Output / Download のリンク
#             link = WebDriverWait(driver, 40).until(
#                 EC.presence_of_element_located((
#                     By.XPATH,
#                     "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'view raw') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'download') or contains(., 'Raw Output')]"
#                 ))
#             )
#             href = link.get_attribute("href")
#         except Exception:
#             # 代替: results.php 等に遷移しているか pre タグ に出力があるか
#             href = None

#         # cookies を requests セッションに移してダウンロードを試みる
#         sess = requests.Session()
#         for c in driver.get_cookies():
#             sess.cookies.set(c['name'], c['value'], domain=c.get('domain', None))

#         if href:
#             data_url = href if href.startswith("http") else urljoin(driver.current_url, href)
#             try:
#                 r = sess.get(data_url, timeout=60)
#                 r.raise_for_status()
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(r.text)
#                 return output_filename
#             except Exception as e:
#                 # フォールバックして、リンクをクリックして表示されたページのテキストを保存
#                 try:
#                     link.click()
#                     time.sleep(1)
#                     txt = driver.page_source
#                     with open(output_filename, "w", encoding="utf-8") as f:
#                         f.write(txt)
#                     return output_filename
#                 except Exception as e2:
#                     return f"エラー（Selenium ダウンロード）: {e} / fallback: {e2}"

#         # href が無い場合はページ内の <pre> を探し生テキストを保存
#         try:
#             pre = driver.find_element(By.TAG_NAME, "pre")
#             txt = pre.text
#             with open(output_filename, "w", encoding="utf-8") as f:
#                 f.write(txt)
#             return output_filename
#         except Exception:
#             # 最終手段: ページ全体のテキストを保存
#             try:
#                 txt = driver.page_source
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(txt)
#                 return output_filename
#             except Exception as e:
#                 return f"エラー（Selenium 最終保存失敗）: {e}"
#     finally:
#         try:
#             driver.quit()
#         except Exception:
#             pass


# def old_run_iri_profile(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float = 80.0,
#     max_alt: float = 2000.0,
#     step_alt: float = 50.0,
#     model_version: str = "IRI-2020",
#     output_filename: str = "iri_profile_output.txt",
#     timeout: float = 30.0
# ) -> str:
#     # ...existing code...
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"

#     session = requests.Session()
#     session.headers.update({
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
#         'Referer': IRI_BASE_URL,
#     })

#     try:
#         r0 = session.get(IRI_BASE_URL, timeout=timeout)
#         r0.raise_for_status()
#     except requests.RequestException as e:
#         return f"エラー（初回GET）: {e}"

#     soup0 = BeautifulSoup(r0.content, "html.parser")
#     form = soup0.find("form")
#     submit_url = IRI_BASE_URL
#     form_method = "post"
#     raw_action = ""
#     if form:
#         if form.get("action"):
#             submit_url = urljoin(IRI_BASE_URL, form.get("action"))
#             raw_action = form.get("action") or ""
#         form_method = (form.get("method") or "post").lower()

#     # debug: show discovered form info
#     print(f"[DEBUG] detected form action -> {submit_url}")
#     print(f"[DEBUG] detected form method -> {form_method}")

#     hidden_inputs = {}
#     for inp in soup0.find_all("input"):
#         n = inp.get("name")
#         t = inp.get("type", "").lower()
#         v = inp.get("value", "")
#         if not n:
#             continue
#         hidden_inputs[n] = v
#     print(f"[DEBUG] discovered form fields (sample): {list(hidden_inputs.keys())[:30]}")

#     # ...build payload as before...
#     payload = {
#         'Year': str(date_time.year),
#         'Month': str(date_time.month),
#         'Day': str(date_time.day),
#         'Hour': str(date_time.hour),
#         'Minute': str(date_time.minute),
#         'Second': str(date_time.second),
#         'ut_type': '0',
#         'Longitude': f"{longitude:.3f}",
#         'Latitude': f"{latitude:.3f}",
#         'coord_type': '0',
#         'min_alt': f"{min_alt:.1f}",
#         'max_alt': f"{max_alt:.1f}",
#         'step_alt': f"{step_alt:.1f}",
#         'alt_type': '0',
#         'grid_type': '0',
#         'model_version': model_version,
#         'opt_F_peak': '0',
#         'opt_F_F1_prob': '1',
#         'opt_F_bottom': '0',
#         'submit_button': 'Submit',
#     }
#     payload.update(hidden_inputs)

#     # collect out_vars if present in the form
#     out_vars_values = []
#     if form:
#         for inp in form.find_all(["input", "select"]):
#             name = inp.get("name", "")
#             if not name:
#                 continue
#             if "out" in name and "var" in name:
#                 if inp.name == "input":
#                     v = inp.get("value")
#                     if v:
#                         out_vars_values.append(v)
#                 elif inp.name == "select":
#                     for opt in inp.find_all("option"):
#                         if opt.get("value"):
#                             out_vars_values.append(opt.get("value"))
#     if not out_vars_values:
#         out_vars_values = ['1','2','3','4','5','6','7','8','9','10']

#     # prepare params/data list (repeated keys allowed)
#     data_list = []
#     for k, v in payload.items():
#         data_list.append((k, str(v)))
#     for v in out_vars_values:
#         data_list.append(("out_vars", str(v)))

#     print(f"[DEBUG] prepared {len(data_list)} form items (showing first 20): {data_list[:20]}")

#     # If the form's action is empty or a JS anchor, switch to Selenium fallback
#     if form is not None:
#         raw = raw_action.strip().lower()
#         if raw in ("", "#") or raw.startswith("javascript"):
#             print("[DEBUG] form action is JS-only -> switching to Selenium")
#             return run_iri_profile_selenium(
#                 date_time=date_time,
#                 longitude=longitude,
#                 latitude=latitude,
#                 min_alt=min_alt,
#                 max_alt=max_alt,
#                 step_alt=step_alt,
#                 model_version=model_version,
#                 output_filename=output_filename,
#                 timeout=timeout*2
#             )

#     # ...existing POST/GET logic (unchanged) ...
#     try:
#         session.headers.update({'Referer': IRI_BASE_URL})
#         if form_method == "get":
#             print(f"[DEBUG] sending GET to {submit_url} with params")
#             r1 = session.get(submit_url, params=data_list, allow_redirects=True, timeout=timeout)
#         else:
#             print(f"[DEBUG] sending POST to {submit_url}")
#             r1 = session.post(submit_url, data=data_list, allow_redirects=True, timeout=timeout)
#         r1.raise_for_status()
#     except requests.RequestException as e:
#         msg = f"エラー（送信）: {e}"
#         try:
#             resp = e.response
#             if resp is not None:
#                 msg += f" (status={resp.status_code}, url={resp.url})\nResponse headers: {resp.headers}\nResponse text head: {resp.text[:800]}"
#         except Exception:
#             pass
#         return msg

#     # ...rest of existing code unchanged...
#     final_url = r1.url
#     final_html = r1.content
#     soup1 = BeautifulSoup(final_html, "html.parser")

#     def find_download_link(soup):
#         tags = soup.find_all("a")
#         for a in tags:
#             href = a.get("href", "")
#             txt = (a.get_text() or "").strip().lower()
#             if not href:
#                 continue
#             if "download" in txt or "view raw" in txt or "raw output" in txt:
#                 return href
#             if href.endswith(".txt") or href.endswith(".out") or "/data/" in href or "output" in href.lower():
#                 return href
#         return None

#     href = find_download_link(soup1)
#     data_url = None
#     if href:
#         data_url = urljoin(final_url, href)
#     else:
#         m = re.search(r"runID=([A-Za-z0-9_\-]+)", final_url)
#         runid = m.group(1) if m else None
#         if not runid:
#             txt = soup1.get_text()
#             m2 = re.search(r"runID=([A-Za-z0-9_\-]+)", txt)
#             runid = m2.group(1) if m2 else None
#         if runid:
#             candidate = urljoin(IRI_BASE_URL, f"data/output_{runid}.txt")
#             data_url = candidate

#     if not data_url:
#         pre = soup1.find("pre")
#         if pre:
#             try:
#                 with open(output_filename, "w", encoding="utf-8") as f:
#                     f.write(pre.get_text())
#                 return output_filename
#             except Exception as e:
#                 return f"エラー（pre保存）: {e}"
#         return "エラー: ダウンロードリンクが見つかりませんでした。フォームの実際の送信はブラウザ(Networkタブ)を確認してください。"

#     try:
#         r2 = session.get(data_url, timeout=timeout)
#         r2.raise_for_status()
#         with open(output_filename, "w", encoding="utf-8") as f:
#             f.write(r2.text)
#         return output_filename
#     except requests.RequestException as e:
#         return f"エラー（データダウンロード）: {e}"
#     except IOError as e:
#         return f"エラー（ファイル書き込み）: {e}"


# def old_run_iri_profile(
#     date_time: datetime,
#     longitude: float,
#     latitude: float,
#     min_alt: float = 80.0,
#     max_alt: float = 2000.0,
#     step_alt: float = 50.0,
#     model_version: str = "IRI-2020",
#     output_filename: str = "iri_profile_output.txt"
# ) -> str:
#     """
#     CCMC IRI Instant Run サービスを使用して、指定された日時・座標での
#     イオン高度プロファイルデータを取得し、ファイルに保存します。

#     このバージョンでは、POST送信先をディレクトリURLに戻し、必要な
#     隠しフィールドを全て抽出し、リクエストヘッダーを強化しています。
#     """
#     print(f"--- IRI Profile Download: {date_time.isoformat()} at Lon={longitude}, Lat={latitude} ---")
    
#     # IRI Instant Run のベースURL (POST送信先としても使用)
#     IRI_BASE_URL = "https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/"
#     SUBMIT_URL = IRI_BASE_URL # ディレクトリURLをPOST送信先とする

#     # セッションを開始し、クッキーとセッションを保持
#     session = requests.Session()
    
#     # 標準ヘッダー設定
#     headers = {
#         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
#         'Referer': IRI_BASE_URL, # 最初のReferer
#         'Content-Type': 'application/x-www-form-urlencoded',
#         'Origin': 'https://kauai.ccmc.gsfc.nasa.gov',
#         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
#         'Accept-Encoding': 'gzip, deflate, br',
#         'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
#         'Connection': 'keep-alive',
#     }
#     session.headers.update(headers)
    
#     # 隠しフィールドを格納する辞書
#     hidden_inputs = {}

#     # -----------------------------------------------------------------
#     # ステップ 0: ページをGETで取得し、セッションと隠しフィールドを確立
#     # -----------------------------------------------------------------
#     try:
#         print(f"0. ページをGETし、セッションと隠しフィールドを確立中... (URL: {IRI_BASE_URL})")
#         get_response = session.get(IRI_BASE_URL)
#         get_response.raise_for_status()
#         print("   => セッション確立完了。")
        
#         # HTMLから全ての隠しフィールドを抽出 (トークン以外のセッション情報も含む)
#         soup = BeautifulSoup(get_response.content, 'html.parser')
        
#         extracted_count = 0
#         for tag in soup.find_all('input', {'type': 'hidden'}):
#             name = tag.get('name')
#             value = tag.get('value', '')
#             if name:
#                 hidden_inputs[name] = value
#                 extracted_count += 1
        
#         if extracted_count > 0:
#             print(f"   => {extracted_count}個の隠しフィールドを抽出しました。")
#         else:
#             print("   => 警告: 隠しフィールドが見つかりませんでした。")
        
#     except requests.exceptions.RequestException as e:
#         return f"エラー（セッション確立/隠しフィールド抽出）: {e}"


#     # -----------------------------------------------------------------
#     # ステップ 1: POSTリクエストで計算を実行 (ディレクトリURLに送信)
#     # -----------------------------------------------------------------
    
#     # ウェブフォームが期待するパラメーターの辞書を作成
#     payload = {
#         # --- Time / Date ---
#         'Year': date_time.year,
#         'Month': date_time.month,
#         'Day': date_time.day,
#         'Hour': date_time.hour,
#         'Minute': date_time.minute,
#         'Second': date_time.second,
#         'ut_type': '0',
        
#         # --- Coordinates ---
#         'Longitude': f"{longitude:.3f}",
#         'Latitude': f"{latitude:.3f}",
#         'coord_type': '0',
        
#         # --- Grid ---
#         'min_alt': f"{min_alt:.1f}",
#         'max_alt': f"{max_alt:.1f}",
#         'step_alt': f"{step_alt:.1f}",
#         'alt_type': '0',
#         'grid_type': '0',
        
#         # --- IRI Model and Options ---
#         'model_version': model_version, 
#         'opt_F_peak': '0', 
#         'opt_F_F1_prob': '1', 
#         'opt_F_bottom': '0', 
        
#         # --- Output Variables ---
#         'out_vars': ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'],
        
#         # --- Submit Action ---
#         'submit_button': 'Submit'
#     }
    
#     # 抽出した全ての隠しフィールドをペイロードに結合
#     payload.update(hidden_inputs) 

#     try:
#         print(f"1. IRI計算をサーバーに送信中... (送信先: {SUBMIT_URL})")
        
#         # POSTリクエストのRefererを現在のURL（IRI_BASE_URL）に設定
#         session.headers.update({'Referer': IRI_BASE_URL})
        
#         response = session.post(SUBMIT_URL, data=payload, allow_redirects=False)

#         # 成功すると302 Foundが返され、Locationヘッダーに結果ページのURLが含まれる
#         if response.status_code == 302 and 'Location' in response.headers:
#             result_url_path = response.headers['Location']
#             print(f"   => 計算要求が受理されました (302リダイレクト)。リダイレクト先: {result_url_path}")
            
#             # リダイレクト先のURLを完全なものにする
#             if not result_url_path.startswith('http'):
#                 # 相対パスの場合、ベースURLと結合
#                 download_url = IRI_BASE_URL + result_url_path.lstrip('/')
#             else:
#                 download_url = result_url_path
#         else:
#             # 302以外のレスポンスはエラーとして扱う
#             response.raise_for_status() # 2xx以外のステータスコードはここでエラーを発生させる
#             return f"エラー（計算送信）: 予期せぬHTTPステータスコード {response.status_code} またはリダイレクトなし。\n応答テキストの先頭: {response.text[:200]}"


#     except requests.exceptions.RequestException as e:
#         return f"エラー（計算送信）: {e}"

#     # -----------------------------------------------------------------
#     # ステップ 2 & 3: 結果ページからダウンロードリンクを抽出し、データをダウンロード
#     # -----------------------------------------------------------------

#     try:
#         # download_url は結果ページ (results.php?...) のURLであると想定
#         print(f"2. 結果ページ({download_url})にアクセスし、ダウンロードリンクを抽出中...")
        
#         # Refererヘッダーを結果ページURLに更新
#         session.headers.update({'Referer': download_url})
        
#         result_page_response = session.get(download_url)
#         result_page_response.raise_for_status()
        
#         soup = BeautifulSoup(result_page_response.content, 'html.parser')
        
#         # 'Download'というテキストを含むアンカータグを検索 (大文字小文字は無視)
#         download_link_tag = soup.find('a', string=lambda t: t and 'download' in t.lower())
        
#         if download_link_tag and 'href' in download_link_tag.attrs:
#             data_url_path = download_link_tag['href']
            
#             if not data_url_path.startswith('http'):
#                 # 相対パスの場合、ベースURLと結合
#                 # ただし、リダイレクト後のURLが'results.php?runID=...'のようにクエリパラメータを持つため、
#                 # data_url_pathが'data/...'であれば、ベースURLから結合すべき
                
#                 # 例: download_urlが https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/results.php?runID=...
#                 # data_url_pathが data/output.txt の場合
#                 # data_download_url = https://kauai.ccmc.gsfc.nasa.gov/instantrun/iri/data/output.txt
                
#                 # パスを正しく解決するために、URLの一部を切り詰めてから結合
#                 base_path = download_url.split('results.php')[0]
#                 data_download_url = base_path + data_url_path.lstrip('/')
#             else:
#                 data_download_url = data_url_path
            
#             print(f"   => データダウンロードURLを抽出しました: {data_download_url}")
            
#             # データファイルのダウンロード
#             print(f"3. データファイル({data_download_url})をダウンロード中...")
#             download_response = session.get(data_download_url)
#             download_response.raise_for_status()
            
#             # ファイルに保存
#             with open(output_filename, 'w', encoding='utf-8') as f:
#                 f.write(download_response.text)
                
#             print(f"   => データは '{output_filename}' に正常に保存されました。")
#             return output_filename
#         else:
#             return "エラー: 結果ページからダウンロードリンクが見つかりませんでした。計算が失敗したか、サイト構造が再度変更された可能性があります。"
        
#     except requests.exceptions.RequestException as e:
#         return f"エラー（ダウンロードまたは結果ページアクセス）: {e}"
#     except IOError as e:
#         return f"エラー（ファイル書き込み）: {e}"

