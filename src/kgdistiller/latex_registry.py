"""Export explicit graph marker identities for native LaTeX documents.

The JSON rows preserve authored spellings. The TeX export stringifies marker
tokens with e-TeX's detokenize primitive; it never expands name macros or uses
document headings/rendered text to guess an identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cli import GraphState


def _active_knowledge(state: GraphState) -> list[dict[str, Any]]:
    return sorted(
        (
            node for node in state.nodes.values()
            if node.get("type") == "knowledge"
            and (node.get("properties") or {}).get("source_status") == "active"
            and (node.get("provenance") or {}).get("active") is not False
        ),
        key=lambda node: str(node["id"]),
    )


def _authored_latex_names(state: GraphState) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in _active_knowledge(state):
        properties = node.get("properties") or {}
        if properties.get("source_format") == "latex":
            result.setdefault(str(node["id"]), []).append(str(properties.get("source_name", "")))
    for reference in state.references:
        if reference.get("source_format") != "latex" or reference.get("origin") != "authored":
            continue
        result.setdefault(str(reference.get("target", "")), []).append(str(reference.get("source_name", "")))
    return result


def marker_registry(state: GraphState) -> list[dict[str, str]]:
    """Return sorted exact spellings for active knowledge-node identities.

    Canonical labels and aliases are graph identity facts. Raw LaTeX authority
    names and authored LaTeX reference names are additional exact spellings.
    A spelling shared by different active identities is an explicit error.
    """
    authored_names = _authored_latex_names(state)
    rows: dict[str, dict[str, str]] = {}
    for node in _active_knowledge(state):
        node_id = str(node["id"])
        properties = node.get("properties") or {}
        provenance = node.get("provenance") or {}
        url = str(provenance.get("web") or f"/knowledge/#node={node_id}")
        names = [str(node.get("label", ""))]
        names.extend(str(alias) for alias in properties.get("aliases", []))
        names.extend(authored_names.get(node_id, []))
        for name in names:
            if not name.strip():
                continue
            existing = rows.get(name)
            if existing and existing["id"] != node_id:
                raise ValueError(
                    f"ambiguous LaTeX marker name {name!r}: {existing['id']!r} and {node_id!r}"
                )
            rows[name] = {"name": name, "id": node_id, "url": url}
    return sorted(rows.values(), key=lambda row: (row["id"], row["name"]))


def _without_tex_comments(value: str) -> str:
    """Remove authored TeX comments without interpreting any macro."""
    pieces: list[str] = []
    cursor = 0
    while cursor < len(value):
        character = value[cursor]
        if character == "%":
            end = value.find("\n", cursor)
            cursor = len(value) if end < 0 else end + 1
        elif character == "\\" and cursor + 1 < len(value):
            end = cursor + 2
            if value[cursor + 1].isascii() and value[cursor + 1].isalpha():
                while end < len(value) and value[end].isascii() and value[end].isalpha():
                    end += 1
            pieces.append(value[cursor:end])
            cursor = end
        else:
            pieces.append(character)
            cursor += 1
    return "".join(pieces)


def _validate_data(value: str, *, name: bool) -> None:
    allowed = "\n\r\t" if name else ""
    if any((ord(character) < 32 and character not in allowed) or ord(character) == 127 for character in value):
        raise ValueError("LaTeX registry data contains an unsupported control character")


def latex_registry_text(state: GraphState) -> str:
    """Generate an input-able preamble file providing kn and knref.

    The file requires hyperref. First definitions establish ``kn-ID`` PDF
    targets; later references use that target when present, otherwise the
    authority/graph URL. Unknown names retain their display and issue a warning.
    Public expandable NodeId/NodeUrl helpers return an empty value for unknowns.
    """
    rows = marker_registry(state)
    authored_names = {
        name for names in _authored_latex_names(state).values() for name in names
    }
    data: list[tuple[str, str, str]] = []
    for row in rows:
        name = _without_tex_comments(row["name"]) if row["name"] in authored_names else row["name"]
        _validate_data(name, name=True)
        _validate_data(row["id"], name=False)
        _validate_data(row["url"], name=False)
        data.append((name, row["id"], row["url"]))

    # A delimited macro reads data without requiring balanced literal braces.
    # Choose a delimiter absent from every value rather than escaping TeX math.
    serial = 0
    boundary = f"|KGDISTILLERBOUNDARY{serial}|"
    while any(boundary in value for row in data for value in row):
        serial += 1
        boundary = f"|KGDISTILLERBOUNDARY{serial}|"

    preamble = r"""% Generated by kgdistiller. Do not edit by hand.
% Load with \input in the document preamble; requires e-TeX and hyperref.
\RequirePackage{hyperref}
\makeatletter
\long\def\kgdistillerNodeId#1{%
  \ifcsname kgd@id@\detokenize{#1}\endcsname
    \csname kgd@id@\detokenize{#1}\endcsname
  \fi}
\long\def\kgdistillerNodeUrl#1{%
  \ifcsname kgd@url@\detokenize{#1}\endcsname
    \csname kgd@url@\detokenize{#1}\endcsname
  \fi}
\long\def\kgd@lookup#1{%
  \edef\kgd@nodeid{\kgdistillerNodeId{#1}}%
  \edef\kgd@nodeurl{\kgdistillerNodeUrl{#1}}}
\DeclareRobustCommand{\kn}[1]{%
  \begingroup
  \kgd@lookup{#1}%
  \ifx\kgd@nodeid\@empty
    \PackageWarning{kgdistiller}{Unknown knowledge marker: \detokenize{#1}}%
    #1%
  \else
    \ifcsname kgd@local@\kgd@nodeid\endcsname
      #1%
    \else
      \expandafter\gdef\csname kgd@local@\kgd@nodeid\endcsname{}%
      \hypertarget{kn-\kgd@nodeid}{#1}%
    \fi
  \fi
  \endgroup}
\DeclareRobustCommand{\knref}[1]{%
  \begingroup
  \kgd@lookup{#1}%
  \ifx\kgd@nodeid\@empty
    \PackageWarning{kgdistiller}{Unknown knowledge marker: \detokenize{#1}}%
    #1%
  \else
    \ifcsname kgd@local@\kgd@nodeid\endcsname
      \hyperlink{kn-\kgd@nodeid}{#1}%
    \else
      \href{\kgd@nodeurl}{#1}%
    \fi
  \fi
  \endgroup}
"""
    registration = (
        rf"\long\def\kgdistillerRegister{boundary}#1{boundary}#2{boundary}#3{boundary}" + r"""{%
  \begingroup
  \edef\kgd@key{\detokenize{#1}}%
  \edef\kgd@nextid{\detokenize{#2}}%
  \ifcsname kgd@id@\kgd@key\endcsname
    \edef\kgd@existingid{\csname kgd@id@\kgd@key\endcsname}%
    \ifx\kgd@existingid\kgd@nextid\else
      \PackageError{kgdistiller}{Conflicting knowledge marker: \kgd@key}{Regenerate the registry after resolving graph identity conflicts.}%
    \fi
  \fi
  \expandafter\xdef\csname kgd@id@\kgd@key\endcsname{\kgd@nextid}%
  \expandafter\xdef\csname kgd@url@\kgd@key\endcsname{\detokenize{#3}}%
  \endgroup}
\begingroup
\catcode37=12\relax
\catcode35=12\relax
\catcode123=12\relax
\catcode125=12\relax
\catcode94=12\relax
\catcode64=12\relax
\catcode126=12\relax
"""
    )
    lines = [preamble, registration]
    lines.extend(
        rf"\kgdistillerRegister{boundary}" + boundary.join(row) + boundary + "\n"
        for row in data
    )
    lines.append("\\endgroup\n\\makeatother\n")
    return "".join(lines)
