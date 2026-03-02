# MCP Apple + Blender

Connecting Claude to creative tools through the Model Context Protocol. This repo packages two MCP server integrations — **Blender 3D** for AI-assisted modeling and **Apple Notes** for programmatic note management — into a unified workspace for exploring what LLM-driven creative tooling looks like in practice.

## Architecture

```
                    ┌───────────────────────┐
                    │      Claude AI        │
                    │   (MCP Client)        │
                    └─────────┬─────────────┘
                              │ MCP Protocol
                    ┌─────────┴─────────────┐
                    │                       │
          ┌─────────▼──────────┐  ┌─────────▼──────────┐
          │  Blender MCP       │  │  Apple Notes MCP   │
          │  Server            │  │  Server             │
          │  (FastMCP + TCP)   │  │  (FastMCP + stdio)  │
          └─────────┬──────────┘  └─────────┬──────────┘
                    │                       │
          ┌─────────▼──────────┐  ┌─────────▼──────────┐
          │  Blender Addon     │  │  osascript          │
          │  (TCP socket       │  │  (AppleScript IPC   │
          │   on :9876)        │  │   to Notes.app)     │
          └─────────┬──────────┘  └────────────────────┘
                    │
          ┌─────────▼──────────┐
          │  Blender 3D        │
          │  + PolyHaven       │
          │  + Hyper3D Rodin   │
          │  + Sketchfab       │
          └────────────────────┘
```

## Blender MCP

Claude directly controls Blender — creating objects, applying materials, importing assets, and executing Python code inside the 3D viewport. Based on [BlenderMCP](https://github.com/ahujasid/blender-mcp) by Siddharth Ahuja.

### Available Tools

| Tool | What it does |
|------|-------------|
| `get_scene_info` | Current scene state (objects, materials, counts) |
| `get_object_info` | Detailed object properties (transform, materials, bbox) |
| `get_viewport_screenshot` | Capture 3D viewport as PNG |
| `execute_blender_code` | Run arbitrary Python in Blender |
| `search_polyhaven_assets` | Search Poly Haven for models, textures, HDRIs |
| `download_polyhaven_asset` | Download + import Poly Haven assets |
| `set_texture` | Apply texture to object |
| `generate_hyper3d_model_via_text` | Generate 3D model from text prompt |
| `generate_hyper3d_model_via_images` | Generate 3D model from reference images |
| `search_sketchfab_models` | Search Sketchfab model library |
| `download_sketchfab_model` | Download + import Sketchfab models |

### Setup

**1. Install the Blender addon**

- Download `addon.py` from this repo
- Blender > Edit > Preferences > Add-ons > Install > select `addon.py`
- Enable "Interface: Blender MCP"

**2. Configure Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

**3. Connect**

- In Blender's 3D View sidebar (N key) > BlenderMCP tab
- Enable integrations (PolyHaven, Hyper3D, Sketchfab) as needed
- Click "Connect to Claude"

### Example Prompts

- "Create a low poly dungeon scene with a dragon guarding gold"
- "Download a marble texture from Poly Haven and apply it to the floor"
- "Generate a 3D garden gnome with Hyper3D"
- "Point the camera at the scene and make it isometric"
- "Get scene info and create a three.js sketch from it"

## Apple Notes MCP

Gives Claude read/write access to Apple Notes via AppleScript IPC. List, search, create, and append to notes without leaving the conversation.

### Available Tools

| Tool | What it does |
|------|-------------|
| `list_notes` | List all notes (filterable by folder, hashtag) |
| `get_note` | Retrieve full note content by name or ID |
| `search_notes` | Search by title or body content |
| `create_note` | Create new note in specified folder |
| `append_to_note` | Append content to existing note |
| `get_changed_notes` | Notes modified after a given timestamp |

### Setup

```bash
cd apple-notes
uv sync
uv run python server.py
```

Requires macOS Automation permissions: System Settings > Privacy & Security > Automation > grant access to your terminal.

## Requirements

- macOS (for Apple Notes integration)
- Blender 3.0+
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- API keys: Anthropic (required), Sketchfab and Hyper3D (optional)

## Communication Protocol

Both servers use JSON-based messaging:

- **Blender**: TCP socket on `localhost:9876` — commands as `{"type": "command", "params": {...}}`, responses as `{"status": "success|error", "result": {...}}`
- **Apple Notes**: MCP over stdio — FastMCP handles protocol framing

## Credits

Blender MCP integration based on [BlenderMCP](https://github.com/ahujasid/blender-mcp) by [Siddharth Ahuja](https://x.com/sidahuj) (MIT License).

## License

MIT
