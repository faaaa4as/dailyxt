use std::{
    cmp, env, fs, io,
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result, anyhow};
use arboard::Clipboard;
use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyModifiers},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Frame, Terminal,
    backend::CrosstermBackend,
    layout::{Alignment, Constraint, Direction, Layout, Margin, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Tabs, Wrap},
};
use serde::{Deserialize, Serialize};

const SUBSTACK_PATH: &str = "config/sources/substacks.json";
const YOUTUBE_PATH: &str = "config/sources/youtube_playlists.json";
const PERSONALITY_PATH: &str = "config/personality/hackernews.md";

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SubstackSource {
    name: String,
    base_url: String,
    feed_url: String,
    priority: i64,
    active: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct SubstackStore {
    publications: Vec<SubstackSource>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct YoutubePlaylist {
    name: String,
    playlist_id: String,
    active: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct YoutubeStore {
    playlists: Vec<YoutubePlaylist>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tab {
    Substacks,
    Youtube,
    Personality,
}

impl Tab {
    fn titles() -> [&'static str; 3] {
        ["Substacks", "YouTube Playlists", "HN Personality"]
    }

    fn next(self) -> Self {
        match self {
            Self::Substacks => Self::Youtube,
            Self::Youtube => Self::Personality,
            Self::Personality => Self::Substacks,
        }
    }

    fn prev(self) -> Self {
        match self {
            Self::Substacks => Self::Youtube,
            Self::Youtube => Self::Personality,
            Self::Personality => Self::Substacks,
        }
    }

    fn index(self) -> usize {
        match self {
            Self::Substacks => 0,
            Self::Youtube => 1,
            Self::Personality => 2,
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum EditorKind {
    AddSubstack,
    EditSubstack(usize),
    AddPlaylist,
    EditPlaylist(usize),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FieldType {
    Text,
    Number,
    Bool,
}

#[derive(Debug, Clone)]
struct FormField {
    label: &'static str,
    value: String,
    kind: FieldType,
}

#[derive(Debug, Clone)]
struct EditorState {
    kind: EditorKind,
    title: String,
    fields: Vec<FormField>,
    selected_field: usize,
}

#[derive(Debug, Clone)]
enum Mode {
    Normal,
    Editing(EditorState),
}

struct App {
    root: PathBuf,
    substack_store: SubstackStore,
    youtube_store: YoutubeStore,
    personality_markdown: String,
    substack_state: ListState,
    youtube_state: ListState,
    tab: Tab,
    mode: Mode,
    status: String,
    should_quit: bool,
}

impl App {
    fn new(root: PathBuf) -> Result<Self> {
        ensure_store_files(&root)?;
        let substack_store = load_substacks(&root)?;
        let youtube_store = load_playlists(&root)?;
        let personality_markdown = load_personality(&root)?;
        let mut substack_state = ListState::default();
        if !substack_store.publications.is_empty() {
            substack_state.select(Some(0));
        }
        let mut youtube_state = ListState::default();
        if !youtube_store.playlists.is_empty() {
            youtube_state.select(Some(0));
        }

        Ok(Self {
            root,
            substack_store,
            youtube_store,
            personality_markdown,
            substack_state,
            youtube_state,
            tab: Tab::Substacks,
            mode: Mode::Normal,
            status: "Loaded sources from JSON.".to_string(),
            should_quit: false,
        })
    }

    fn selected_substack(&self) -> Option<usize> {
        self.substack_state.selected()
    }

    fn selected_playlist(&self) -> Option<usize> {
        self.youtube_state.selected()
    }

    fn next_item(&mut self) {
        match self.tab {
            Tab::Substacks => {
                let len = self.substack_store.publications.len();
                if len == 0 {
                    self.substack_state.select(None);
                    return;
                }
                let next = match self.selected_substack() {
                    Some(i) => (i + 1) % len,
                    None => 0,
                };
                self.substack_state.select(Some(next));
            }
            Tab::Youtube => {
                let len = self.youtube_store.playlists.len();
                if len == 0 {
                    self.youtube_state.select(None);
                    return;
                }
                let next = match self.selected_playlist() {
                    Some(i) => (i + 1) % len,
                    None => 0,
                };
                self.youtube_state.select(Some(next));
            }
            Tab::Personality => {}
        }
    }

    fn previous_item(&mut self) {
        match self.tab {
            Tab::Substacks => {
                let len = self.substack_store.publications.len();
                if len == 0 {
                    self.substack_state.select(None);
                    return;
                }
                let prev = match self.selected_substack() {
                    Some(0) | None => len - 1,
                    Some(i) => i - 1,
                };
                self.substack_state.select(Some(prev));
            }
            Tab::Youtube => {
                let len = self.youtube_store.playlists.len();
                if len == 0 {
                    self.youtube_state.select(None);
                    return;
                }
                let prev = match self.selected_playlist() {
                    Some(0) | None => len - 1,
                    Some(i) => i - 1,
                };
                self.youtube_state.select(Some(prev));
            }
            Tab::Personality => {}
        }
    }

    fn switch_tab(&mut self, forward: bool) {
        self.tab = if forward {
            self.tab.next()
        } else {
            self.tab.prev()
        };
    }

    fn open_add_editor(&mut self) {
        self.mode = match self.tab {
            Tab::Substacks => Mode::Editing(EditorState {
                kind: EditorKind::AddSubstack,
                title: "Add Substack".to_string(),
                fields: vec![
                    FormField {
                        label: "Name",
                        value: String::new(),
                        kind: FieldType::Text,
                    },
                    FormField {
                        label: "Base URL",
                        value: String::new(),
                        kind: FieldType::Text,
                    },
                    FormField {
                        label: "Feed URL",
                        value: String::new(),
                        kind: FieldType::Text,
                    },
                    FormField {
                        label: "Priority",
                        value: (self.substack_store.publications.len() + 1).to_string(),
                        kind: FieldType::Number,
                    },
                    FormField {
                        label: "Active",
                        value: "true".to_string(),
                        kind: FieldType::Bool,
                    },
                ],
                selected_field: 0,
            }),
            Tab::Youtube => Mode::Editing(EditorState {
                kind: EditorKind::AddPlaylist,
                title: "Add YouTube Playlist".to_string(),
                fields: vec![
                    FormField {
                        label: "Name",
                        value: String::new(),
                        kind: FieldType::Text,
                    },
                    FormField {
                        label: "Playlist ID or URL",
                        value: String::new(),
                        kind: FieldType::Text,
                    },
                    FormField {
                        label: "Active",
                        value: "true".to_string(),
                        kind: FieldType::Bool,
                    },
                ],
                selected_field: 0,
            }),
            Tab::Personality => {
                self.status =
                    "Use c to copy prompt, p to import LLM response from clipboard.".to_string();
                Mode::Normal
            }
        };
    }

    fn open_edit_editor(&mut self) {
        self.mode = match self.tab {
            Tab::Substacks => {
                let Some(index) = self.selected_substack() else {
                    self.status = "No Substack selected.".to_string();
                    return;
                };
                let item = &self.substack_store.publications[index];
                Mode::Editing(EditorState {
                    kind: EditorKind::EditSubstack(index),
                    title: format!("Edit Substack #{}", index + 1),
                    fields: vec![
                        FormField {
                            label: "Name",
                            value: item.name.clone(),
                            kind: FieldType::Text,
                        },
                        FormField {
                            label: "Base URL",
                            value: item.base_url.clone(),
                            kind: FieldType::Text,
                        },
                        FormField {
                            label: "Feed URL",
                            value: item.feed_url.clone(),
                            kind: FieldType::Text,
                        },
                        FormField {
                            label: "Priority",
                            value: item.priority.to_string(),
                            kind: FieldType::Number,
                        },
                        FormField {
                            label: "Active",
                            value: item.active.to_string(),
                            kind: FieldType::Bool,
                        },
                    ],
                    selected_field: 0,
                })
            }
            Tab::Youtube => {
                let Some(index) = self.selected_playlist() else {
                    self.status = "No playlist selected.".to_string();
                    return;
                };
                let item = &self.youtube_store.playlists[index];
                Mode::Editing(EditorState {
                    kind: EditorKind::EditPlaylist(index),
                    title: format!("Edit Playlist #{}", index + 1),
                    fields: vec![
                        FormField {
                            label: "Name",
                            value: item.name.clone(),
                            kind: FieldType::Text,
                        },
                        FormField {
                            label: "Playlist ID or URL",
                            value: item.playlist_id.clone(),
                            kind: FieldType::Text,
                        },
                        FormField {
                            label: "Active",
                            value: item.active.to_string(),
                            kind: FieldType::Bool,
                        },
                    ],
                    selected_field: 0,
                })
            }
            Tab::Personality => {
                self.status =
                    "Personality file is updated via clipboard import, not row edit.".to_string();
                Mode::Normal
            }
        };
    }

    fn delete_selected(&mut self) -> Result<()> {
        match self.tab {
            Tab::Substacks => {
                if let Some(index) = self.selected_substack() {
                    self.substack_store.publications.remove(index);
                    normalize_substack_priorities(&mut self.substack_store.publications);
                    save_substacks(&self.root, &self.substack_store)?;
                    let new_index =
                        bounded_selection(index, self.substack_store.publications.len());
                    self.substack_state.select(new_index);
                    self.status = "Deleted Substack entry.".to_string();
                }
            }
            Tab::Youtube => {
                if let Some(index) = self.selected_playlist() {
                    self.youtube_store.playlists.remove(index);
                    save_playlists(&self.root, &self.youtube_store)?;
                    let new_index = bounded_selection(index, self.youtube_store.playlists.len());
                    self.youtube_state.select(new_index);
                    self.status = "Deleted playlist entry.".to_string();
                }
            }
            Tab::Personality => {
                self.status =
                    "Personality file not deleted; import a replacement with p.".to_string();
            }
        }
        Ok(())
    }

    fn toggle_selected_active(&mut self) -> Result<()> {
        match self.tab {
            Tab::Substacks => {
                let Some(index) = self.selected_substack() else {
                    return Ok(());
                };
                let (name, active) = {
                    let item = &mut self.substack_store.publications[index];
                    item.active = !item.active;
                    (item.name.clone(), item.active)
                };
                save_substacks(&self.root, &self.substack_store)?;
                self.status = format!("Substack '{}' active={}.", name, active);
            }
            Tab::Youtube => {
                let Some(index) = self.selected_playlist() else {
                    return Ok(());
                };
                let (name, active) = {
                    let item = &mut self.youtube_store.playlists[index];
                    item.active = !item.active;
                    (item.name.clone(), item.active)
                };
                save_playlists(&self.root, &self.youtube_store)?;
                self.status = format!("Playlist '{}' active={}.", name, active);
            }
            Tab::Personality => {
                self.status = "Personality profile has no active flag.".to_string();
            }
        }
        Ok(())
    }

    fn copy_personality_prompt(&mut self) -> Result<()> {
        let prompt = personality_prompt();
        let mut clipboard = Clipboard::new().context("failed to open clipboard")?;
        clipboard
            .set_text(prompt)
            .context("failed to copy prompt to clipboard")?;
        self.status = "Copied HN personality prompt to clipboard.".to_string();
        Ok(())
    }

    fn import_personality_from_clipboard(&mut self) -> Result<()> {
        let mut clipboard = Clipboard::new().context("failed to open clipboard")?;
        let response = clipboard.get_text().context("failed to read clipboard")?;
        let markdown = extract_personality_markdown(&response)?;
        save_personality(&self.root, &markdown)?;
        self.personality_markdown = markdown;
        self.status = "Imported HN personality markdown from clipboard.".to_string();
        Ok(())
    }

    fn save_editor(&mut self) -> Result<()> {
        let Mode::Editing(editor) = &self.mode else {
            return Ok(());
        };

        match editor.kind {
            EditorKind::AddSubstack | EditorKind::EditSubstack(_) => {
                let mut item = substack_from_fields(&editor.fields)?;
                if item.feed_url.trim().is_empty() {
                    item.feed_url = derive_substack_feed_url(&item.base_url);
                }
                match editor.kind {
                    EditorKind::AddSubstack => self.substack_store.publications.push(item),
                    EditorKind::EditSubstack(index) => {
                        self.substack_store.publications[index] = item
                    }
                    _ => {}
                }
                normalize_substack_priorities(&mut self.substack_store.publications);
                save_substacks(&self.root, &self.substack_store)?;
                let index = match editor.kind {
                    EditorKind::AddSubstack => {
                        self.substack_store.publications.len().saturating_sub(1)
                    }
                    EditorKind::EditSubstack(index) => cmp::min(
                        index,
                        self.substack_store.publications.len().saturating_sub(1),
                    ),
                    _ => 0,
                };
                self.substack_state.select(Some(index));
                self.status = "Saved Substack sources.".to_string();
            }
            EditorKind::AddPlaylist | EditorKind::EditPlaylist(_) => {
                let item = playlist_from_fields(&editor.fields)?;
                match editor.kind {
                    EditorKind::AddPlaylist => self.youtube_store.playlists.push(item),
                    EditorKind::EditPlaylist(index) => self.youtube_store.playlists[index] = item,
                    _ => {}
                }
                save_playlists(&self.root, &self.youtube_store)?;
                let index = match editor.kind {
                    EditorKind::AddPlaylist => self.youtube_store.playlists.len().saturating_sub(1),
                    EditorKind::EditPlaylist(index) => {
                        cmp::min(index, self.youtube_store.playlists.len().saturating_sub(1))
                    }
                    _ => 0,
                };
                self.youtube_state.select(Some(index));
                self.status = "Saved YouTube playlists.".to_string();
            }
        }

        self.mode = Mode::Normal;
        Ok(())
    }
}

fn main() -> Result<()> {
    let root = find_project_root()?;
    enable_raw_mode().context("failed to enable raw mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen).context("failed to enter alternate screen")?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend).context("failed to create terminal")?;
    let result = run_app(&mut terminal, App::new(root)?);
    disable_raw_mode().ok();
    execute!(terminal.backend_mut(), LeaveAlternateScreen).ok();
    terminal.show_cursor().ok();
    result
}

fn run_app(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>, mut app: App) -> Result<()> {
    while !app.should_quit {
        terminal.draw(|frame| ui(frame, &app))?;
        if event::poll(Duration::from_millis(200))? {
            let Event::Key(key) = event::read()? else {
                continue;
            };
            handle_key_event(&mut app, key)?;
        }
    }
    Ok(())
}

fn handle_key_event(app: &mut App, key: KeyEvent) -> Result<()> {
    match &mut app.mode {
        Mode::Normal => match key.code {
            KeyCode::Char('q') => app.should_quit = true,
            KeyCode::Tab => app.switch_tab(true),
            KeyCode::BackTab => app.switch_tab(false),
            KeyCode::Down | KeyCode::Char('j') => app.next_item(),
            KeyCode::Up | KeyCode::Char('k') => app.previous_item(),
            KeyCode::Char('a') => app.open_add_editor(),
            KeyCode::Char('e') => app.open_edit_editor(),
            KeyCode::Char('d') => app.delete_selected()?,
            KeyCode::Char(' ') => app.toggle_selected_active()?,
            KeyCode::Char('c') if app.tab == Tab::Personality => app.copy_personality_prompt()?,
            KeyCode::Char('p') if app.tab == Tab::Personality => {
                app.import_personality_from_clipboard()?
            }
            KeyCode::Char('s') => {
                save_substacks(&app.root, &app.substack_store)?;
                save_playlists(&app.root, &app.youtube_store)?;
                save_personality(&app.root, &app.personality_markdown)?;
                app.status = "Saved source and personality files.".to_string();
            }
            _ => {}
        },
        Mode::Editing(editor) => match key.code {
            KeyCode::Esc => {
                app.mode = Mode::Normal;
                app.status = "Edit cancelled.".to_string();
            }
            KeyCode::Tab => {
                editor.selected_field = (editor.selected_field + 1) % editor.fields.len()
            }
            KeyCode::BackTab => {
                editor.selected_field = if editor.selected_field == 0 {
                    editor.fields.len() - 1
                } else {
                    editor.selected_field - 1
                };
            }
            KeyCode::Enter => app.save_editor()?,
            KeyCode::Backspace => {
                if let Some(field) = editor.fields.get_mut(editor.selected_field) {
                    if field.kind != FieldType::Bool {
                        field.value.pop();
                    }
                }
            }
            KeyCode::Char(' ') => {
                if let Some(field) = editor.fields.get_mut(editor.selected_field) {
                    if field.kind == FieldType::Bool {
                        field.value = toggle_bool_text(&field.value);
                    } else {
                        field.value.push(' ');
                    }
                }
            }
            KeyCode::Char(c) => {
                if key.modifiers.contains(KeyModifiers::CONTROL) {
                    return Ok(());
                }
                if let Some(field) = editor.fields.get_mut(editor.selected_field) {
                    match field.kind {
                        FieldType::Bool => {
                            if matches!(c, 't' | 'T') {
                                field.value = "true".to_string();
                            } else if matches!(c, 'f' | 'F') {
                                field.value = "false".to_string();
                            }
                        }
                        FieldType::Number => {
                            if c.is_ascii_digit() {
                                field.value.push(c);
                            }
                        }
                        FieldType::Text => field.value.push(c),
                    }
                }
            }
            _ => {}
        },
    }
    Ok(())
}

fn ui(frame: &mut Frame, app: &App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(2),
        ])
        .split(frame.area());

    let tabs = Tabs::new(Tab::titles())
        .select(app.tab.index())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Content Manager"),
        )
        .highlight_style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        );
    frame.render_widget(tabs, outer[0]);

    let content = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(outer[1]);

    render_list_panel(frame, app, content[0]);
    render_detail_panel(frame, app, content[1]);

    let footer = Paragraph::new(Line::from(vec![
        Span::styled("tab", Style::default().fg(Color::Cyan)),
        Span::raw(" switch  "),
        Span::styled("j/k", Style::default().fg(Color::Cyan)),
        Span::raw(" move  "),
        Span::styled("a", Style::default().fg(Color::Cyan)),
        Span::raw(" add  "),
        Span::styled("e", Style::default().fg(Color::Cyan)),
        Span::raw(" edit  "),
        Span::styled("d", Style::default().fg(Color::Cyan)),
        Span::raw(" delete  "),
        Span::styled("space", Style::default().fg(Color::Cyan)),
        Span::raw(" toggle active  "),
        Span::styled("enter", Style::default().fg(Color::Cyan)),
        Span::raw(" save form  "),
        Span::styled("c/p", Style::default().fg(Color::Cyan)),
        Span::raw(" personality copy/import  "),
        Span::styled("q", Style::default().fg(Color::Cyan)),
        Span::raw(" quit"),
    ]))
    .block(Block::default().borders(Borders::ALL).title("Keys"));
    frame.render_widget(footer, outer[2]);

    let status_area = Rect {
        x: outer[2].x + 2,
        y: outer[2].y,
        width: outer[2].width.saturating_sub(4),
        height: 1,
    };
    frame.render_widget(
        Paragraph::new(app.status.as_str()).alignment(Alignment::Right),
        status_area,
    );

    if let Mode::Editing(editor) = &app.mode {
        render_editor_popup(frame, editor);
    }
}

fn render_list_panel(frame: &mut Frame, app: &App, area: Rect) {
    match app.tab {
        Tab::Substacks => {
            let items: Vec<ListItem> = app
                .substack_store
                .publications
                .iter()
                .map(|item| {
                    let status = if item.active { "active" } else { "inactive" };
                    ListItem::new(Line::from(format!(
                        "#{:02} {} ({})",
                        item.priority, item.name, status
                    )))
                })
                .collect();
            let list = List::new(items)
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title("Substack Sources"),
                )
                .highlight_style(Style::default().fg(Color::Black).bg(Color::Yellow))
                .highlight_symbol(">> ");
            let mut state = app.substack_state.clone();
            frame.render_stateful_widget(list, area, &mut state);
        }
        Tab::Youtube => {
            let items: Vec<ListItem> = app
                .youtube_store
                .playlists
                .iter()
                .map(|item| {
                    let status = if item.active { "active" } else { "inactive" };
                    ListItem::new(Line::from(format!("{} ({})", item.name, status)))
                })
                .collect();
            let list = List::new(items)
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title("YouTube Playlists"),
                )
                .highlight_style(Style::default().fg(Color::Black).bg(Color::Yellow))
                .highlight_symbol(">> ");
            let mut state = app.youtube_state.clone();
            frame.render_stateful_widget(list, area, &mut state);
        }
        Tab::Personality => {
            let items = vec![ListItem::new(Line::from(
                "config/personality/hackernews.md",
            ))];
            let list = List::new(items)
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title("HN Personality"),
                )
                .highlight_style(Style::default().fg(Color::Black).bg(Color::Yellow))
                .highlight_symbol(">> ");
            frame.render_widget(list, area);
        }
    }
}

fn render_detail_panel(frame: &mut Frame, app: &App, area: Rect) {
    let text = match app.tab {
        Tab::Substacks => app
            .selected_substack()
            .and_then(|index| app.substack_store.publications.get(index))
            .map(|item| {
                vec![
                    Line::from(vec![Span::styled(&item.name, Style::default().add_modifier(Modifier::BOLD))]),
                    Line::from(""),
                    Line::from(format!("Base URL: {}", item.base_url)),
                    Line::from(format!("Feed URL: {}", item.feed_url)),
                    Line::from(format!("Priority: {}", item.priority)),
                    Line::from(format!("Active: {}", item.active)),
                    Line::from(""),
                    Line::from("Tip: leave Feed URL blank when adding and it will be derived from Base URL."),
                ]
            })
            .unwrap_or_else(|| vec![Line::from("No Substack selected.")]),
        Tab::Youtube => app
            .selected_playlist()
            .and_then(|index| app.youtube_store.playlists.get(index))
            .map(|item| {
                vec![
                    Line::from(vec![Span::styled(&item.name, Style::default().add_modifier(Modifier::BOLD))]),
                    Line::from(""),
                    Line::from(format!("Playlist ID: {}", item.playlist_id)),
                    Line::from(format!("Active: {}", item.active)),
                    Line::from(""),
                    Line::from("Tip: you can paste a full playlist URL; the TUI will extract the list= value."),
                ]
            })
            .unwrap_or_else(|| vec![Line::from("No playlist selected.")]),
        Tab::Personality => {
            let preview: Vec<Line> = app
                .personality_markdown
                .lines()
                .take(22)
                .map(Line::from)
                .collect();
            let mut lines = vec![
                Line::from(vec![Span::styled("HN personality profile", Style::default().add_modifier(Modifier::BOLD))]),
                Line::from(""),
                Line::from("c: copy onboarding prompt for your LLM"),
                Line::from("p: import the LLM response from clipboard"),
                Line::from("Accepted format: Markdown containing one fenced content-hub-personality JSON object."),
                Line::from(""),
            ];
            lines.extend(preview);
            lines
        }
    };

    let block_title = match app.tab {
        Tab::Substacks => "Details",
        Tab::Youtube => "Details",
        Tab::Personality => "Personality Markdown",
    };
    let paragraph = Paragraph::new(text)
        .block(Block::default().borders(Borders::ALL).title(block_title))
        .wrap(Wrap { trim: false });
    frame.render_widget(paragraph, area);
}

fn render_editor_popup(frame: &mut Frame, editor: &EditorState) {
    let area = centered_rect(70, 70, frame.area());
    frame.render_widget(Clear, area);
    let block = Block::default()
        .borders(Borders::ALL)
        .title(editor.title.as_str());
    frame.render_widget(block, area);

    let inner = area.inner(Margin {
        horizontal: 2,
        vertical: 1,
    });
    let mut lines: Vec<Line> = Vec::new();
    for (idx, field) in editor.fields.iter().enumerate() {
        let label_style = if idx == editor.selected_field {
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::Gray)
        };
        let value_style = if idx == editor.selected_field {
            Style::default().fg(Color::Black).bg(Color::Yellow)
        } else {
            Style::default()
        };
        lines.push(Line::from(vec![
            Span::styled(format!("{:<18}", field.label), label_style),
            Span::styled(field.value.as_str(), value_style),
        ]));
        lines.push(Line::from(""));
    }
    lines.push(Line::from(
        "Tab/Shift+Tab move between fields. Enter saves. Esc cancels.",
    ));

    let paragraph = Paragraph::new(lines).wrap(Wrap { trim: false });
    frame.render_widget(paragraph, inner);
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}

fn bounded_selection(previous_index: usize, len: usize) -> Option<usize> {
    if len == 0 {
        None
    } else {
        Some(cmp::min(previous_index, len - 1))
    }
}

fn toggle_bool_text(value: &str) -> String {
    if value.eq_ignore_ascii_case("true") {
        "false".to_string()
    } else {
        "true".to_string()
    }
}

fn parse_bool(value: &str) -> Result<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "yes" | "y" | "1" => Ok(true),
        "false" | "no" | "n" | "0" => Ok(false),
        _ => Err(anyhow!("expected true/false for active field")),
    }
}

fn substack_from_fields(fields: &[FormField]) -> Result<SubstackSource> {
    let name = fields[0].value.trim().to_string();
    let mut base_url = normalize_url(&fields[1].value);
    let feed_url = normalize_url(&fields[2].value);
    let priority = fields[3]
        .value
        .trim()
        .parse::<i64>()
        .context("priority must be a positive integer")?;
    let active = parse_bool(&fields[4].value)?;
    if name.is_empty() {
        return Err(anyhow!("name cannot be empty"));
    }
    if base_url.is_empty() && !feed_url.is_empty() {
        base_url = feed_url.trim_end_matches("/feed").to_string();
    }
    if base_url.is_empty() {
        return Err(anyhow!("base URL or feed URL cannot be empty"));
    }
    Ok(SubstackSource {
        name,
        base_url,
        feed_url,
        priority,
        active,
    })
}

fn playlist_from_fields(fields: &[FormField]) -> Result<YoutubePlaylist> {
    let name = fields[0].value.trim().to_string();
    let playlist_id = extract_playlist_id(fields[1].value.trim());
    let active = parse_bool(&fields[2].value)?;
    if name.is_empty() {
        return Err(anyhow!("playlist name cannot be empty"));
    }
    if playlist_id.is_empty() {
        return Err(anyhow!("playlist ID cannot be empty"));
    }
    Ok(YoutubePlaylist {
        name,
        playlist_id,
        active,
    })
}

fn normalize_substack_priorities(items: &mut Vec<SubstackSource>) {
    items.sort_by_key(|item| item.priority);
    for (idx, item) in items.iter_mut().enumerate() {
        item.priority = (idx + 1) as i64;
    }
}

fn normalize_url(input: &str) -> String {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        trimmed.trim_end_matches('/').to_string()
    } else {
        format!("https://{}", trimmed.trim_end_matches('/'))
    }
}

fn derive_substack_feed_url(base_url: &str) -> String {
    let base = normalize_url(base_url);
    if base.ends_with("/feed") {
        base
    } else {
        format!("{}/feed", base)
    }
}

fn extract_playlist_id(value: &str) -> String {
    let trimmed = value.trim();
    if let Some(pos) = trimmed.find("list=") {
        let tail = &trimmed[pos + 5..];
        let end = tail.find('&').unwrap_or(tail.len());
        return tail[..end].to_string();
    }
    trimmed.to_string()
}

fn find_project_root() -> Result<PathBuf> {
    let cwd = env::current_dir().context("failed to read current directory")?;
    for candidate in cwd.ancestors() {
        if candidate.join("pyproject.toml").exists() && candidate.join("config").exists() {
            return Ok(candidate.to_path_buf());
        }
    }
    Err(anyhow!(
        "could not find project root from current directory"
    ))
}

fn ensure_store_files(root: &Path) -> Result<()> {
    let substack_path = root.join(SUBSTACK_PATH);
    let youtube_path = root.join(YOUTUBE_PATH);
    let personality_path = root.join(PERSONALITY_PATH);
    if let Some(parent) = substack_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Some(parent) = youtube_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Some(parent) = personality_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if !substack_path.exists() {
        save_json(&substack_path, &SubstackStore::default())?;
    }
    if !youtube_path.exists() {
        save_json(&youtube_path, &YoutubeStore::default())?;
    }
    if !personality_path.exists() {
        save_personality(root, DEFAULT_PERSONALITY_MARKDOWN)?;
    }
    Ok(())
}

fn load_substacks(root: &Path) -> Result<SubstackStore> {
    load_json(&root.join(SUBSTACK_PATH))
}

fn load_playlists(root: &Path) -> Result<YoutubeStore> {
    load_json(&root.join(YOUTUBE_PATH))
}

fn save_substacks(root: &Path, store: &SubstackStore) -> Result<()> {
    save_json(&root.join(SUBSTACK_PATH), store)
}

fn save_playlists(root: &Path, store: &YoutubeStore) -> Result<()> {
    save_json(&root.join(YOUTUBE_PATH), store)
}

fn load_personality(root: &Path) -> Result<String> {
    fs::read_to_string(root.join(PERSONALITY_PATH)).context("failed to read HN personality file")
}

fn save_personality(root: &Path, markdown: &str) -> Result<()> {
    fs::write(root.join(PERSONALITY_PATH), markdown).context("failed to write HN personality file")
}

const DEFAULT_PERSONALITY_MARKDOWN: &str = r#"# Hacker News Personality Profile

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "Describe the reader's durable interests, preferred depth, and what makes an HN item worth attention.",
  "positive_keywords": {
    "llm": 2.5,
    "developer tools": 2.0,
    "database": 2.0,
    "security": 1.6
  },
  "negative_keywords": {
    "celebrity": -2.5,
    "drama": -3.0
  }
}
```
"#;

fn personality_prompt() -> String {
    r#"You are helping configure a Hacker News ranking profile for Content Hub.
Interview me or infer from what I tell you. Return ONLY Markdown in the accepted format below.

Goal: capture durable user preferences for ranking HN titles. Use concise, lowercase keywords/phrases.
Weights: positive interests usually 0.5 to 5.0; core obsessions may be 6.0 to 10.0. Negative dislikes should be negative numbers, usually -0.5 to -8.0.
Do not include secrets, names, private addresses, or temporary tasks.

Accepted format:
# Hacker News Personality Profile

Short human-readable notes are allowed outside the fence.

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "One paragraph describing preferred topics, depth, tone, and exclusion rules.",
  "positive_keywords": {
    "keyword or phrase": 2.0
  },
  "negative_keywords": {
    "keyword or phrase": -2.0
  }
}
```

Return only that Markdown."#.to_string()
}

fn extract_personality_markdown(response: &str) -> Result<String> {
    let start = response
        .find("```content-hub-personality")
        .ok_or_else(|| anyhow!("missing ```content-hub-personality fenced block"))?;
    let after_lang = response[start..]
        .find('\n')
        .map(|pos| start + pos + 1)
        .ok_or_else(|| anyhow!("personality fence must contain JSON on following lines"))?;
    let rest = &response[after_lang..];
    let end_rel = rest
        .find("```")
        .ok_or_else(|| anyhow!("missing closing ``` for personality block"))?;
    let json_text = rest[..end_rel].trim();
    let value: serde_json::Value =
        serde_json::from_str(json_text).context("personality JSON is invalid")?;
    if value.get("schema_version").and_then(|v| v.as_i64()) != Some(1) {
        return Err(anyhow!("personality schema_version must be 1"));
    }
    if !value
        .get("positive_keywords")
        .is_some_and(|v| v.is_object())
    {
        return Err(anyhow!("positive_keywords must be an object"));
    }
    if !value
        .get("negative_keywords")
        .is_some_and(|v| v.is_object())
    {
        return Err(anyhow!("negative_keywords must be an object"));
    }
    for field in ["positive_keywords", "negative_keywords"] {
        let object = value[field].as_object().expect("checked above");
        for (keyword, weight) in object {
            if keyword.trim().is_empty() || !weight.is_number() {
                return Err(anyhow!(
                    "{field} must map non-empty keywords to numeric weights"
                ));
            }
        }
    }
    Ok(response.trim().to_string() + "\n")
}

fn load_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let text =
        fs::read_to_string(path).with_context(|| format!("failed to read {}", path.display()))?;
    serde_json::from_str(&text).with_context(|| format!("failed to parse {}", path.display()))
}

fn save_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    let text = serde_json::to_string_pretty(value)?;
    fs::write(path, format!("{text}\n"))
        .with_context(|| format!("failed to write {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_personality_markdown_accepts_required_schema() {
        let input = r#"# Hacker News Personality Profile

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "Prefer compilers and databases.",
  "positive_keywords": {"compiler": 3.0, "database": 2.0},
  "negative_keywords": {"celebrity": -4.0}
}
```
"#;
        let markdown = extract_personality_markdown(input).unwrap();
        assert!(markdown.contains("```content-hub-personality"));
        assert!(markdown.contains("compiler"));
    }

    #[test]
    fn extract_personality_markdown_rejects_missing_schema() {
        let input = r#"```content-hub-personality
{"positive_keywords": {}, "negative_keywords": {}}
```"#;
        let err = extract_personality_markdown(input).unwrap_err().to_string();
        assert!(err.contains("schema_version"));
    }

    #[test]
    fn personality_prompt_documents_accepted_format() {
        let prompt = personality_prompt();
        assert!(prompt.contains("```content-hub-personality"));
        assert!(prompt.contains("positive_keywords"));
        assert!(prompt.contains("negative_keywords"));
    }

    #[test]
    fn substack_form_accepts_feed_url_without_base_url() {
        let fields = vec![
            FormField {
                label: "Name",
                value: "Example".to_string(),
                kind: FieldType::Text,
            },
            FormField {
                label: "Base URL",
                value: "".to_string(),
                kind: FieldType::Text,
            },
            FormField {
                label: "Feed URL",
                value: "example.substack.com/feed".to_string(),
                kind: FieldType::Text,
            },
            FormField {
                label: "Priority",
                value: "1".to_string(),
                kind: FieldType::Number,
            },
            FormField {
                label: "Active",
                value: "true".to_string(),
                kind: FieldType::Bool,
            },
        ];
        let source = substack_from_fields(&fields).unwrap();
        assert_eq!(source.base_url, "https://example.substack.com");
        assert_eq!(source.feed_url, "https://example.substack.com/feed");
    }
}
