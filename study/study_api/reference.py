"""Public reference lookups for the selection toolbar (dictionary + Wikipedia)."""

from __future__ import annotations

import asyncio
import concurrent.futures

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services import reference_lookup
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


def _run_async(coro):
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if in_loop:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


class DictionaryOut(BaseModel):
    word: str
    part_of_speech: str | None
    definition: str
    example: str | None


class WikipediaOut(BaseModel):
    title: str
    extract: str
    source_url: str


@router.get("/dictionary", response_model=DictionaryOut, dependencies=[Depends(rate_limit_dependency)])
def dictionary_lookup(
    word: str = Query(..., min_length=1, max_length=120),
) -> DictionaryOut:
    """Free dictionary definition for a selected word or short phrase."""
    try:
        entry = _run_async(reference_lookup.lookup_dictionary(word))
    except reference_lookup.ReferenceLookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DictionaryOut(
        word=entry.word,
        part_of_speech=entry.part_of_speech,
        definition=entry.definition,
        example=entry.example,
    )


@router.get("/wikipedia", response_model=WikipediaOut, dependencies=[Depends(rate_limit_dependency)])
def wikipedia_lookup(
    query: str = Query(..., min_length=1, max_length=200),
) -> WikipediaOut:
    """English Wikipedia summary for a selected term."""
    try:
        summary = _run_async(reference_lookup.lookup_wikipedia_summary(query))
    except reference_lookup.ReferenceLookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WikipediaOut(
        title=summary.title,
        extract=summary.extract,
        source_url=summary.source_url,
    )
