# 汎用・家庭用 CPU ティア表

## 概要
一般向け・家庭用 PC 構成における CPU 選定のための公式ティア表です。予算帯別に許可される CPU ティアと、CPU 分類パターンを定義します。

Intel 第13世代 / 第14世代（Core i3/i5/i7/i9 の 13xxx / 14xxx 系番）は、ティア判定前に除外します。
例: i5-13400F, i7-14700KF, i9 14900 KS などのサフィックス・区切り揺れも除外対象です。

AMD Ryzen 9 の **9950X / 9950X3D / 9950X3D2** はプレミアム予算帯のみの対象です。
汎用・家庭用の Enthusiast ティアは **9900X までに制限** して差別化を図ります。

---

## 1. 予算帯別ティア許可表

予算ティア（budget_tier）に基づき、選定可能な CPU ティアを制限します。

| 予算帯 | 許可ティア | 説明 |
|---------|-----------|------|
| **low** (低予算) | entry, mainstream, performance, enthusiast | すべてのティアを候補に含める（ただし Intel 第13/14世代は除外） |
| **middle** (中予算) | mainstream, performance, enthusiast | entry ティア CPU は除外（Athlon, Sempron など）|
| **high** (高予算) | performance, enthusiast | mainstream 以下は除外（9950X系は除外） |
| **premium** (プレミアム) | performance, enthusiast | 9950X / 9950X3D / 9950X3D2 を含む |

---

## 2. CPU 分類パターン表

CPU 名から自動的にティアを判定するためのパターンマッチング規則。

### Entry ティア（エントリー級）
対象 CPU: 低価格・低性能向け

注記: Intel の `core i3` は分類上 entry ですが、第13/14世代（例: i3-13100, i3-14100）は除外対象です。

| メーカー | パターン | 例 |
|---------|---------|-----|
| AMD | `athlon` | Athlon 3000G |
| AMD | `sempron` | Sempron |
| AMD | `ryzen 3` | Ryzen 3 4100 |
| Intel | `pentium` | Pentium Gold |
| Intel | `celeron` | Celeron |
| Intel | `core i3` | Core i3-12100（13/14世代は除外） |
| Intel | `n{3桁数字}` | N95, N100 |
| 旧世代 | `processor 300` | Processor 300 |

### Mainstream ティア（メインストリーム級）
対象 CPU: 一般向け・バランス型

注記: Intel の `core i5` は分類上 mainstream ですが、第13/14世代（例: i5-13400, i5-14400）は除外対象です。

| メーカー | パターン | 例 |
|---------|---------|-----|
| AMD | `ryzen 5` | Ryzen 5 5500, Ryzen 5 7600 |
| Intel | `core i5` | Core i5-12400（13/14世代は除外） |
| Intel | `core ultra 5` | Core™ Ultra 5 250K Plus |

### Performance ティア（高性能級）
対象 CPU: ゲーム・クリエイター向け

注記: Intel の `core i7` は分類上 performance ですが、第13/14世代（例: i7-13700K, i7-14700K）は除外対象です。

| メーカー | パターン | 例 |
|---------|---------|-----|
| AMD | `ryzen 7` | Ryzen 7 5700X, Ryzen 7 9700X |
| Intel | `core i7` | Core i7-12700K（13/14世代は除外） |
| Intel | `core ultra 7` | Core™ Ultra 7 265K, 265KF |

### Enthusiast ティア（エンスージアスト級）
対象 CPU: ハイエンド・ワークステーション向け

注記: Intel の `core i9` は分類上 enthusiast ですが、第13/14世代（例: i9-13900K, i9-14900K）は除外対象です。
注記: AMD Ryzen 9 の **9950X / 9950X3D / 9950X3D2** は汎用・家庭用では不可（プレミアムのみ）。汎用は **9900X** までに制限。

| メーカー | パターン | 例 |
|---------|---------|-----|
| AMD | `ryzen 9` (9900X まで) | Ryzen 9 9900X |
| Intel | `core i9` (12世代以下) | Core i9-12900K（13/14世代は除外） |
| Intel | `core ultra 9` | Core™ Ultra 9 285K |


---

## 3. コア数フォールバック表

パターンマッチングで判定できない場合、コア数から自動分類します。

| コア数 | 判定ティア |
|--------|----------|
| ≤ 4 | entry |
| 5-6 | mainstream |
| 7-8 | performance |
| ≥ 9 | enthusiast |

---

## 4. 選定アルゴリズム

```
1. 候補 CPU リストから GENERAL_HOME_CPU_ALLOWED_TIERS_BY_BUDGET で許可ティアを確認
2. 各 CPU について _classify_general_home_cpu_tier() で分類:
  a. Intel 第13/14世代（13xxx/14xxx）を先に除外
  b. CPU 名を正規化（™, ® 記号を削除）
  c. GENERAL_HOME_CPU_NAME_PATTERNS_BY_TIER のパターンでマッチング
  d. パターン未該当の場合、_extract_cpu_core_count() でコア数を取得し判定
3. 許可ティアに該当する CPU のみを候補に絞込
4. ティア絞込後が空の場合は、除外適用後の候補でフォールバック
```

---

## 5. 使用例

### 例1: 中予算（middle）・AMD Ryzen
```
予算帯: middle
許可ティア: {mainstream, performance, enthusiast}

候補:
  - Athlon 3000G → entry (除外)
  - Ryzen 5 5500 → mainstream (含む) ✓
  - Ryzen 7 7700X → performance (含む) ✓

結果: Ryzen 5 5500, Ryzen 7 7700X を候補に
```

### 例2: 高予算（high）・Intel Core
```
予算帯: high
許可ティア: {performance, enthusiast}

候補:
  - Core i5-13400 → 第13世代のため除外
  - Core i7-13700K → 第13世代のため除外
  - Core i9-13900K → 第13世代のため除外
  - Core Ultra 7 265KF → performance (含む) ✓
  - Core Ultra 9 285K → enthusiast (含む) ✓

結果: Core Ultra 7 265KF, Core Ultra 9 285K を候補に
```

### 例3: 低予算（low）・混合
```
予算帯: low
許可ティア: {entry, mainstream, performance, enthusiast}

候補:
  - Celeron N5105 → entry (含む) ✓
  - Ryzen 5 5600G → mainstream (含む) ✓
  - Core i7-12700 → performance (含む) ✓

結果: すべて候補に含まれる
```

---

## 6. 実装詳細

### ティア判定関数
```python
def _classify_general_home_cpu_tier(part) -> str:
    """
    CPU part を 'entry', 'mainstream', 'performance', 'enthusiast' に分類
    """
```

### ティア絞込関数
```python
def _filter_general_home_cpu_by_tier_table(candidates, budget_tier) -> list:
    """
    候補 CPU を予算帯の許可ティアで絞込
    """
```

### コア数下限関数
```python
def _general_home_cpu_min_core_floor(budget_tier) -> int:
    """
    予算帯別のコア数下限値を返す
    low: 4, middle: 6, high: 8, premium: 8
    """
```

---

## 更新履歴

| 日付 | 内容 |
|------|------|
| 2026-05-10 | 初版作成（entry/mainstream/performance/enthusiast 4ティア） |
| 2026-05-10 | Intel 第13世代 / 第14世代の除外ルールを追加 |
| 2026-05-10 | Core Ultra 250系 優先ルール実装完了（バックエンド & フロントエンド） |
| 2026-05-10 | Ryzen 9 9950X系をプレミアムのみに制限（汎用は9900Xまで） |

---

## 実装確認

### バックエンド実装 ✅ 完了

**ファイル:** `django/scraper/views.py`

**実装内容:**
- `_is_intel_core_ultra_250_series(part)`: Core Ultra 250系判定関数
- `_is_intel_core_ultra_265_series(part)`: Core Ultra 265系判定関数
- `_is_excluded_intel_generation_cpu(part)`: Intel 13/14世代除外関数
- `_is_premium_only_cpu(part)`: Ryzen 9 9950X系（プレミアムのみ）判定関数
- `_prefer_general_spec_cpu_quality_pool()`: 250系優先ルールの適用
- `_filter_general_home_cpu_by_tier_table()`: ティア絞込と 9950X系除外

**ロジック:**
1. general/business/standard での CPU 選定時、9950X系を自動除外
2. premium 予算帯でのみ 9950X / 9950X3D / 9950X3D2 を許可
3. 汎用側は Ryzen 9 9900X までに制限

**テスト:** ✅ 3件全て PASSED（既存）

### フロントエンド実装 ✅ 不要

**理由:** フロントエンド (React + Vite) は、バックエンドAPI (`/api/generate-config/`) から返ってくる結果をそのまま表示する設計。

**API統合:**
```
[POST] /api/generate-config/
  ↓ (REQUEST)
{
  "budget": 247478,
  "usage": "general",
  "build_priority": "spec",
  "cpu_vendor": "intel",
  ...
}
  ↓ (RESPONSE)
バックエンド側で250系優先ルール適用済みの結果
  ↓
フロントエンド表示
```

**検証方法:**
1. フロントエンドで `general + spec + 中予算` で構成生成
2. CPU欄に **Core Ultra 5 250K Plus** が表示されることを確認
3. 265KF が選ばれないことを確認
