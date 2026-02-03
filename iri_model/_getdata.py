import re
import os
import numpy as np
from datetime import datetime, timedelta, UTC
import glob
from common import display, time


def extract_iri_profile_data(filepath):
    """
    IRIプロファイル出力テキストから高度(H)とイオン組成比を抽出します。
    
    IRI出力ファイルの構造に基づき、以下の列を抽出します:
    - H (高度, km): 1列目
    - O+ composition (%): 8列目
    - NO+ composition (%): 9列目
    - O2+ composition (%): 10列目

    Parameters
    ----------
    file_content : str
        iri_profile_output.txt のファイル内容全体を読み込んだ文字列。

    Returns
    -------
    list of lists: 
        各リストは [H (km), O+ (%), NO+ (%), O2+ (%)] のデータ行です。
        データ行が検出されなかった場合は空のリストを返します。
    """
    if not os.path.exists(filepath):
        raise ValueError(f'No such a file: {filepath}')
    
    if not filepath.endswith('txt'):
        raise ValueError(f'Input file must be txt: {filepath}')
    
    file_content = open(filepath, "r").read()
    
    extracted_data = []
    
    # ファイル内容を行ごとに分割
    lines = file_content.split('\n')

    # time_str
    time_str = None
    
    if len(lines) >= 4:
        header_line = lines[3]

        match = re.search(r'(\d{4})/\s*(-?\d+)/\s*([\d\.]+)UT', header_line)
        
        if match:
            year_str = match.group(1) 
            doy_str = match.group(2)  
            ut_hour_str = match.group(3)

            year = int(year_str)
            # DOYは符号を無視して整数化 (例: '-42' -> 42)。IRIのDOYは1から始まる。
            doy = int(doy_str.lstrip('-').strip())
            ut_hour = float(ut_hour_str)
            
            # 1. 日付の計算 (YYYY-mm-dd)
            # 1月1日からDOY-1日後の日付を計算 (うるう年にも対応)
            date_obj = datetime(year, 1, 1) + timedelta(days=doy - 1)

            # 2. 時刻の計算 (HH:MM:SS)
            hours = int(ut_hour)
            minutes_float = (ut_hour - hours) * 60.0
            minutes = int(minutes_float)
            
            seconds_float = (minutes_float - minutes) * 60.0
            seconds = int(round(seconds_float))
            
            # 3. timedelta を使って日時を結合し、繰り上げを自動処理
            time_delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            # date_objに時間情報を加算（時間繰り上げも自動処理）
            final_datetime = date_obj.replace(hour=0, minute=0, second=0, microsecond=0) + time_delta

            # 4. 指定フォーマット (YYYY-mm-dd HH:MM:SS) での出力
            time_str = final_datetime.strftime('%Y-%m-%d %H:%M:%S')

    if time_str is None:
        return
    
    # プロファイルデータが始まる行を見つける
    # IRI出力では、プロファイルデータは通常、最初の数行のヘッダー情報（空行も含む）
    # の後に始まります。最初の数値データ行を探します。
    data_start_found = False
    
    for line in lines:
        # 空白を複数のスペースとみなし、連続する空白を1つのスペースに置換
        cleaned_line = re.sub(r'\s+', ' ', line.strip())
        
        # 行が空でないことを確認
        if not cleaned_line:
            continue
            
        # 最初の要素が数値（高度）で始まっているかチェックし、
        # データ行かどうかを判断します。
        # IRIのデータ行は、例えば " 1800.0 15105 0.012 1030 3869 3869 23 8 900 68 0 0 -1 39.7 63" のように、
        # 最初の数値の後に続くデータが豊富です。
        
        try:
            # 高度 (H) は常に浮動小数点数です
            parts = cleaned_line.split()
            if len(parts) < 10: # 必要な列（HからO2+まで）が揃っているか確認
                continue
                
            # 最初の要素が数値に変換可能か確認
            H = float(parts[0])
            
            # 抽出した文字列を浮動小数点数に変換
            data_row = []
            for i in range(len(parts)):
                data_row.append(parts[i])
            
            extracted_data.append(data_row)
            data_start_found = True
            
        except ValueError:
            # 最初の要素が数値でなかった場合（ヘッダー行、説明行、空行など）
            # データ行が既に始まっていれば、抽出を続行する（今回は不要だが、一般的なパース処理として）
            # データ行開始前であればスキップ
            if data_start_found:
                # データ行が終わったと判断し、処理を終了しても良いが、
                # IRI出力は通常、データブロックが連続するため、単にスキップ
                pass 

    
    dict_return = {}

    if not extracted_data:
        return

    # list -> ndarray
    dict_return['time_str'] = time_str
    dict_return['time_unix'] = time.convert(time_str, frm='str', into='unix')
    extracted_data = np.array(extracted_data, dtype=float)

    keys = [
        'altitude', # [km]
        'Ne', # [/cm^3]
        'Ne/NmF2', # ratio
        'Tn', # [K]
        'Ti', # [K]
        'Te', # [K]
        'O+', # [%]*10
        'N+', # [%]*10
        'H+', # [%]*10
        'He+', # [%]*10
        'O2+', # [%]*10
        'NO+', # [%]*10
        'Clust', # 1e16 [m^2]
        'TEC', # 1e16 [m^2]
        't/%', # 1e16 [m^2]
    ]

    for i, key in enumerate(keys):
        dict_return[key] = extracted_data[:, i]
    
    # -> MKSA unit
    dict_return['altitude'] *= 1e3 # [m]
    dict_return['Ne'] *= 1e6 # [/m^3]
    dict_return['O+'] /= 10 # [%]
    dict_return['N+'] /= 10 # [%]
    dict_return['H+'] /= 10 # [%]
    dict_return['He+'] /= 10 # [%]
    dict_return['O2+'] /= 10 # [%]
    dict_return['NO+'] /= 10 # [%]
    dict_return['Clust'] *= 1e16 # [m^2]
    dict_return['TEC'] *= 1e16 # [m^2]
    dict_return['t/%'] *= 1e16 # [m^2]

    return dict_return


# def get_array_iri(
#         txtfile_list,
# ):
#     dict_return = {
#         'times': [],
#         'altitude': [],
#         'Ne': [],
#         'O+': [],
#         'N+': [],
#         'H+': [],
#         'He+': [],
#         'O2+': [],
#         'NO+': [],
#     }
#     vars = ['Ne', 'O+', 'N+', 'He+', 'O2+', 'NO+']
#     data_flag = False
#     for txtfile_path in txtfile_list:
#         try:
#             dict_data = extract_iri_profile_data(txtfile_path)
#             if not data_flag:
#                 dict_return['altitude'] = dict_data['altitude']
#             if not dict_data is None:
#                 data_flag = True
#             dict_return['times'].append(dict_data['times_unix'])
#             for var in vars:
#                 dict_return[var].append(dict_data[var])

#         except Exception as e:
#             print(f'Error: {e}')
#     return dict_return




# def get_dict_iri(txt_filepath):
#     extracted_data = extract_iri_profile_data(txt_filepath)
#     keys = [
#         'altitude', # [km]
#         'Ne', # [/cm^3]
#         'Ne/NmF2', # ratio
#         'Tn', # [K]
#         'Ti', # [K]
#         'Te', # [K]
#         'O+', # [%]*10
#         'N+', # [%]*10
#         'H+', # [%]*10
#         'He+', # [%]*10
#         'O2+', # [%]*10
#         'NO+', # [%]*10
#         'Clust', # 1e16 [m^2]
#         'TEC', # 1e16 [m^2]
#         't/%', # 1e16 [m^2]
#     ]

#     dict_return = {}
#     for i, key in enumerate(keys):
#         dict_return[key] = extracted_data[:, i]
    
#     # -> MKSA unit
#     dict_return['altitude'] *= 1e3 # [m]
#     dict_return['Ne'] *= 1e6 # [/m^3]
#     dict_return['O+'] /= 10 # [%]
#     dict_return['N+'] /= 10 # [%]
#     dict_return['H+'] /= 10 # [%]
#     dict_return['He+'] /= 10 # [%]
#     dict_return['O2+'] /= 10 # [%]
#     dict_return['NO+'] /= 10 # [%]
#     dict_return['Clust'] *= 1e16 # [m^2]
#     dict_return['TEC'] *= 1e16 # [m^2]
#     dict_return['t/%'] *= 1e16 # [m^2]

#     return dict_return



