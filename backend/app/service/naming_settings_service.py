"""Naming settings preview, import, export, and diff helpers."""

from dataclasses import asdict
import json

from app.core.config import AppSettings
from app.schema.media import ParsedMediaName
from app.schema.settings import NamingTemplateBundle, NamingTemplateDiffResult, NamingTemplateMetadata, NamingTemplatePreviewResult
from app.service.settings_service import get_effective_settings, validate_setting_value
from app.utils.naming_template import build_preview_name_with_settings

NAMING_TEMPLATE_SCHEMA_VERSION = 1


def get_naming_template_metadata(settings: AppSettings, media_type: str) -> NamingTemplateMetadata:
    """Return current naming template metadata for one media type."""

    effective = get_effective_settings(settings)
    template_key, version_key, updated_at_key = _template_keys(media_type)
    return NamingTemplateMetadata(
        media_type=media_type,
        template_key=template_key,
        template_value=str(effective.get(template_key) or ""),
        template_version=int(effective.get(version_key) or 1),
        template_updated_at=str(effective.get(updated_at_key) or ""),
    )


def export_naming_template_bundle(settings: AppSettings) -> NamingTemplateBundle:
    """Return the current effective naming template bundle."""

    effective = get_effective_settings(settings)
    return NamingTemplateBundle(
        schema_version=NAMING_TEMPLATE_SCHEMA_VERSION,
        movie_template=str(effective.get("naming.movie_template") or ""),
        episode_template=str(effective.get("naming.episode_template") or ""),
        separator=str(effective.get("naming.separator") or "."),
        keep_year=bool(effective.get("naming.keep_year")),
    )


def validate_naming_template_bundle_text(settings: AppSettings, raw_text: str) -> NamingTemplateBundle:
    """Parse and validate one naming template import bundle."""

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("导入文件不是有效的 JSON") from exc
    return validate_naming_template_bundle_payload(settings, payload)


def validate_naming_template_bundle_payload(
    settings: AppSettings,
    payload: object,
) -> NamingTemplateBundle:
    """Validate the naming template bundle structure and template values."""

    if isinstance(payload, (list, str)):
        raise ValueError("导入文件缺少模板类型信息")
    if not isinstance(payload, dict):
        raise ValueError("导入文件格式不正确")

    schema_version = _parse_schema_version(payload.get("schema_version", NAMING_TEMPLATE_SCHEMA_VERSION))
    movie_template = str(payload.get("movie_template") or "").strip()
    episode_template = str(payload.get("episode_template") or "").strip()
    if not movie_template or not episode_template:
        raise ValueError("导入文件缺少电影或剧集模板")

    effective = get_effective_settings(settings)
    return NamingTemplateBundle(
        schema_version=schema_version,
        movie_template=str(
            validate_setting_value(
                "naming.movie_template",
                movie_template,
                current_effective=effective,
            )
        ),
        episode_template=str(
            validate_setting_value(
                "naming.episode_template",
                episode_template,
                current_effective=effective,
            )
        ),
        separator=str(
            validate_setting_value(
                "naming.separator",
                payload.get("separator", "."),
                current_effective=effective,
            )
        ),
        keep_year=bool(
            validate_setting_value(
                "naming.keep_year",
                payload.get("keep_year", True),
                current_effective=effective,
            )
        ),
    )


def build_naming_template_preview(
    settings: AppSettings,
    media_type: str,
    sample: dict[str, object],
    template: str | None = None,
    separator: str | None = None,
    keep_year: bool | None = None,
) -> NamingTemplatePreviewResult:
    """Generate a filename preview using current or candidate naming settings."""

    effective = get_effective_settings(settings)
    preview_settings = _merged_preview_settings(effective, media_type, template, separator, keep_year)
    parsed, extension = _parsed_media_from_sample(media_type, sample)
    generated_name = build_preview_name_with_settings(parsed, extension, preview_settings)
    metadata = get_naming_template_metadata(settings, media_type)
    return NamingTemplatePreviewResult(
        media_type=media_type,
        generated_name=generated_name,
        template_version=metadata.template_version,
        template_updated_at=metadata.template_updated_at,
    )


def build_naming_template_diff(
    settings: AppSettings,
    media_type: str,
    sample: dict[str, object],
    template: str | None = None,
    separator: str | None = None,
    keep_year: bool | None = None,
) -> NamingTemplateDiffResult:
    """Compare current naming output with a candidate template configuration."""

    effective = get_effective_settings(settings)
    parsed, extension = _parsed_media_from_sample(media_type, sample)
    current_generated_name = build_preview_name_with_settings(parsed, extension, effective)
    candidate_settings = _merged_preview_settings(effective, media_type, template, separator, keep_year)
    candidate_generated_name = build_preview_name_with_settings(parsed, extension, candidate_settings)
    metadata = get_naming_template_metadata(settings, media_type)
    return NamingTemplateDiffResult(
        media_type=media_type,
        current_generated_name=current_generated_name,
        candidate_generated_name=candidate_generated_name,
        changed=current_generated_name != candidate_generated_name,
        template_version=metadata.template_version,
        template_updated_at=metadata.template_updated_at,
    )


def naming_template_preview_to_dict(result: NamingTemplatePreviewResult) -> dict[str, object]:
    """Convert preview result to API payload."""

    return asdict(result)


def naming_template_diff_to_dict(result: NamingTemplateDiffResult) -> dict[str, object]:
    """Convert diff result to API payload."""

    return asdict(result)


def naming_template_bundle_to_dict(bundle: NamingTemplateBundle) -> dict[str, object]:
    """Convert naming bundle to API payload."""

    return asdict(bundle)


def _template_keys(media_type: str) -> tuple[str, str, str]:
    if media_type == "episode":
        return (
            "naming.episode_template",
            "naming.episode_template_version",
            "naming.episode_template_updated_at",
        )
    if media_type == "movie":
        return (
            "naming.movie_template",
            "naming.movie_template_version",
            "naming.movie_template_updated_at",
        )
    raise ValueError("media_type 仅支持 movie 或 episode")


def _merged_preview_settings(
    effective: dict[str, object],
    media_type: str,
    template: str | None,
    separator: str | None,
    keep_year: bool | None,
) -> dict[str, object]:
    template_key, _, _ = _template_keys(media_type)
    merged = dict(effective)
    if template is not None:
        merged[template_key] = validate_setting_value(template_key, template, current_effective=effective)
    if separator is not None:
        merged["naming.separator"] = validate_setting_value("naming.separator", separator, current_effective=effective)
    if keep_year is not None:
        merged["naming.keep_year"] = bool(keep_year)
    return merged


def _parsed_media_from_sample(media_type: str, sample: dict[str, object]) -> tuple[ParsedMediaName, str]:
    title = str(sample.get("title") or "").strip()
    if not title:
        raise ValueError("sample.title 不能为空")
    year = _optional_int(sample.get("year"))
    season = _optional_int(sample.get("season"))
    episode = _optional_int(sample.get("episode"))
    extension = str(sample.get("extension") or "").strip() or (".mp4" if media_type == "episode" else ".mkv")
    extra_raw = sample.get("extra")
    extra: dict[str, str] = {}
    if isinstance(extra_raw, dict):
        extra = {str(key): "" if value is None else str(value) for key, value in extra_raw.items()}
    return ParsedMediaName(media_type=media_type, title=title, year=year, season=season, episode=episode, extra=extra), extension


def _parse_schema_version(value: object) -> int:
    try:
        schema_version = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("导入文件的模板版本不受支持") from exc
    if schema_version < 1:
        raise ValueError("导入文件的模板版本不受支持")
    return schema_version


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
