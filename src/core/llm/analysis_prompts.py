"""Prompt template for on-demand topic analysis."""

from __future__ import annotations

TOPIC_ANALYSIS_PROMPT = """{SYSTEM}
너는 텔레그램 주식 피드 분석가다.
사용자가 지정한 주제/태그에 대해 최근 피드 흐름을 분석한다.
반드시 한국어로만 출력한다. JSON 외 텍스트는 출력하지 않는다.
추론 과정은 짧게 한 줄로 끝내고 바로 JSON을 출력한다.

출력 스키마:
{{
  "summary": ["한 줄 요약 1", "한 줄 요약 2", "한 줄 요약 3", "한 줄 요약 4", "한 줄 요약 5"],
  "timeline": [{{"date": "YYYY-MM-DD", "note": "한 줄"}}],
  "trend_change": "과거 대비 최근 차이를 한 문단으로",
  "importance_reasons": ["이유1", "이유2"],
  "watchlist": ["확인할 원문/근거1", "확인할 원문/근거2"],
  "uncertainties": ["불확실한 부분1", "불확실한 부분2"],
  "interest_for_user": "관심 분야와의 연관성 한 문단"
}}

{INPUT}
{
  "target": "{{target}}",
  "period": "{{period}}",
  "feeds": {{feeds_json}},
  "daily_metrics": {{daily_metrics_json}},
  "user_interests": {{user_interests_json}}
}

{OUTPUT_JSON_SCHEMA}
{{
  "summary": ["string"],
  "timeline": [{{"date": "string", "note": "string"}}],
  "trend_change": "string",
  "importance_reasons": ["string"],
  "watchlist": ["string"],
  "uncertainties": ["string"],
  "interest_for_user": "string"
}}
"""


def render_analysis_prompt(
    *,
    target: str,
    period: str,
    feeds_json: str,
    daily_metrics_json: str,
    user_interests_json: str,
) -> str:
    return (
        TOPIC_ANALYSIS_PROMPT
        .replace("{{target}}", target)
        .replace("{{period}}", period)
        .replace("{{feeds_json}}", feeds_json)
        .replace("{{daily_metrics_json}}", daily_metrics_json)
        .replace("{{user_interests_json}}", user_interests_json)
    )
