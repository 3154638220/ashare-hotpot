from __future__ import annotations

from pathlib import Path


COLOR_BACKGROUND = "#0B1017"
COLOR_SURFACE = "#111821"
COLOR_SURFACE_RAISED = "#17212C"
COLOR_BORDER = "#263241"
COLOR_TEXT = "#E8EEF6"
COLOR_MUTED = "#96A4B5"
COLOR_LINK = "#4D8DFF"
COLOR_HOT = "#FF5D68"
COLOR_SUCCESS = "#32C991"
COLOR_WARNING = "#F4B740"

# White checkmark used by checked QCheckBox indicators; resolved as an
# absolute path so the stylesheet works both from the source tree and from a
# PyInstaller onedir bundle (assets are collected into the package).
_CHECK_SVG = (Path(__file__).resolve().parent / "assets" / "icons" / "check.svg").as_posix()


DARK_STYLESHEET = f"""
QMainWindow, QDialog {{ background: {COLOR_BACKGROUND}; }}
QWidget {{
    color: {COLOR_TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QToolBar#commandBar {{
    background: {COLOR_SURFACE};
    border: 0;
    border-bottom: 1px solid {COLOR_BORDER};
    spacing: 6px;
    padding: 6px 12px;
}}
QToolBar#commandBar::separator {{ width: 1px; background: {COLOR_BORDER}; margin: 8px 6px; }}
QLabel#brandTitle {{ font-size: 15px; font-weight: 700; padding-right: 8px; }}
QLabel#researchGroupLabel {{ color: {COLOR_MUTED}; font-size: 12px; font-weight: 700; padding: 0 6px; }}
QLabel#viewTitle {{ font-size: 17px; font-weight: 700; }}
QLabel#mutedLabel, QLabel#viewSubtitle, QLabel#toolLabel, QLabel#detailMeta {{ color: {COLOR_MUTED}; }}
QLabel#viewSubtitle, QLabel#detailMeta {{ font-size: 12px; }}
QDialog#aboutDialog {{ background: {COLOR_BACKGROUND}; }}
QFrame#aboutHero {{ background: #101C2B; border: 1px solid #29486A; border-radius: 10px; }}
QLabel#aboutTitle {{ color: {COLOR_TEXT}; font-size: 22px; font-weight: 700; }}
QLabel#aboutSubtitle {{ color: {COLOR_MUTED}; font-size: 12px; }}
QLabel#aboutVersion {{
    color: #CFE2FF; background: #1A3350; border: 1px solid #315E90;
    border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: 700;
}}
QLabel#aboutUpdateStatus {{ color: {COLOR_MUTED}; font-size: 12px; }}
QFrame#aboutInfoCard {{ background: {COLOR_SURFACE}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; }}
QFrame#aboutInfoCard[risk="true"] {{ background: #211A18; border-color: #5A4031; }}
QLabel#aboutCardTitle {{ color: {COLOR_TEXT}; font-weight: 700; }}
QLabel#aboutCardText {{ color: {COLOR_MUTED}; font-size: 12px; }}
QLabel#freshnessLabel {{ color: {COLOR_MUTED}; padding: 0 8px; }}
QLabel#freshnessLabel[state="fresh"] {{ color: {COLOR_SUCCESS}; }}
QLabel#freshnessLabel[state="stale"] {{ color: {COLOR_WARNING}; }}
QLabel#freshnessLabel[state="error"] {{ color: {COLOR_HOT}; }}
QFrame#viewHeader, QFrame#tablePanel, QFrame#detailPanel, QFrame#emptyState {{
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
}}
QFrame#tableTools, QFrame#detailHeader {{ border: 0; border-bottom: 1px solid {COLOR_BORDER}; }}
QFrame#kpiChip {{ background: {COLOR_SURFACE_RAISED}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; }}
QLabel#kpiLabel {{ color: {COLOR_MUTED}; font-size: 11px; }}
QLabel#kpiValue {{ color: {COLOR_TEXT}; font-weight: 700; }}
QFrame#qualityPanel {{ background: #121B25; border: 1px solid {COLOR_BORDER}; border-radius: 6px; }}
QFrame#researchBanner {{ background: #2A2518; border: 1px solid #655228; border-radius: 6px; }}
QLabel#researchBannerText {{ color: {COLOR_WARNING}; font-weight: 700; }}
QLabel#researchSectionTitle {{ color: {COLOR_TEXT}; font-size: 13px; font-weight: 700; padding: 6px 2px 2px 2px; }}
QLabel#researchExcerpt {{ color: {COLOR_MUTED}; font-size: 12px; }}
QFrame#errorBanner {{ background: #2A171B; border: 1px solid #71313B; border-radius: 6px; }}
QLabel#errorText {{ color: #FFB3BA; }}
QPushButton, QToolButton {{
    min-height: 30px;
    padding: 0 12px;
    background: {COLOR_SURFACE_RAISED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    color: {COLOR_TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background: #202C39; border-color: #3C5068; }}
QPushButton:pressed, QToolButton:pressed {{ background: #0F151D; }}
QPushButton:disabled, QToolButton:disabled {{ color: #5F6C7C; background: #121922; border-color: #202A37; }}
QPushButton#primaryButton, QToolButton#primaryButton {{ background: {COLOR_LINK}; border-color: {COLOR_LINK}; color: white; font-weight: 700; }}
QPushButton#primaryButton:hover, QToolButton#primaryButton:hover {{ background: #679FFF; border-color: #679FFF; }}
QPushButton#dangerButton {{ color: #FF9CA5; }}
QPushButton#sourceTab {{ background: transparent; border-color: transparent; color: {COLOR_MUTED}; font-weight: 600; }}
QPushButton#sourceTab:hover {{ background: #192331; color: {COLOR_TEXT}; }}
QPushButton#sourceTab:checked {{ background: #1A2A40; border-color: #315887; color: #DCEAFF; }}
QToolButton#moreButton {{ font-size: 19px; font-weight: 700; padding: 0 9px; }}
QToolButton#qualityToggle {{ background: transparent; border-color: transparent; color: {COLOR_MUTED}; padding: 0 4px; }}
QLineEdit, QSpinBox, QComboBox {{
    min-height: 30px;
    padding: 0 9px;
    background: #0D141D;
    border: 1px solid #334256;
    border-radius: 6px;
    color: {COLOR_TEXT};
    selection-background-color: #315887;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #4A607B; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {COLOR_LINK}; }}
QLineEdit#searchInput {{ padding-left: 0; }}
QComboBox QAbstractItemView {{
    background: {COLOR_SURFACE_RAISED};
    border: 1px solid #35455A;
    color: {COLOR_TEXT};
    selection-background-color: #23344A;
    selection-color: {COLOR_TEXT};
    outline: 0;
}}
QTextBrowser, QTextEdit {{
    background: #0E151E;
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 6px;
    color: {COLOR_TEXT};
    selection-background-color: #315887;
}}
QTableView, QTreeWidget, QListWidget {{
    background: #0E151E;
    alternate-background-color: #111A24;
    border: 0;
    color: {COLOR_TEXT};
    gridline-color: #1F2A37;
    selection-background-color: #1D3553;
    selection-color: {COLOR_TEXT};
    outline: 0;
}}
QTableView::item, QTreeWidget::item, QListWidget::item {{ padding: 0 8px; border: 0; }}
QTableView::item:hover, QTreeWidget::item:hover, QListWidget::item:hover {{ background: #172536; }}
QHeaderView::section {{
    background: #151F2A;
    border: 0;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    color: #B8C5D5;
    padding: 8px;
    font-size: 12px;
    font-weight: 600;
}}
QProgressBar {{ min-height: 5px; max-height: 5px; border: 0; border-radius: 2px; background: #263444; }}
QProgressBar::chunk {{ background: {COLOR_LINK}; border-radius: 2px; }}
QStatusBar {{ background: #0E141C; border-top: 1px solid {COLOR_BORDER}; color: {COLOR_MUTED}; }}
QStatusBar::item {{ border: 0; }}
QMenu {{ background: {COLOR_SURFACE_RAISED}; color: {COLOR_TEXT}; border: 1px solid #35455A; padding: 6px; }}
QMenu::item {{ padding: 8px 32px 8px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: #23344A; }}
QMenu::item:disabled {{ color: #667487; }}
QMenu::separator {{ height: 1px; background: {COLOR_BORDER}; margin: 5px 8px; }}
QMenu#industryFilterMenu {{ padding: 0; }}
QWidget#industryFilterPanel, QWidget#industryFilterOptions {{ background: {COLOR_SURFACE_RAISED}; }}
QScrollArea#industryFilterScroll {{ background: #0E151E; }}
QCheckBox#industryFilterOption {{ background: #0E151E; border-radius: 4px; padding: 6px 4px; }}
QCheckBox#industryFilterOption:hover {{ background: #172536; }}
QPushButton#industryFilterControl {{ min-height: 28px; }}
QScrollArea {{ background: #0E151E; border: 1px solid {COLOR_BORDER}; border-radius: 6px; }}
QCheckBox {{ padding: 5px 3px; }}
QCheckBox:hover {{ background: #172536; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #4A607B;
    border-radius: 4px;
    background: #0D141D;
}}
QCheckBox::indicator:hover {{ border-color: #679FFF; background: #16202C; }}
QCheckBox::indicator:checked {{
    background: {COLOR_LINK};
    border-color: {COLOR_LINK};
    image: url({_CHECK_SVG});
}}
QCheckBox::indicator:disabled {{ border-color: #334256; background: #121922; }}
QScrollBar:vertical {{ width: 10px; background: #0E151E; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; background: #35465B; border-radius: 5px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 10px; background: #0E151E; margin: 2px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; background: #35465B; border-radius: 5px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {COLOR_BACKGROUND}; width: 6px; }}
QSplitter::handle:hover {{ background: #24344A; }}
QTabWidget::pane {{ border: 1px solid {COLOR_BORDER}; border-radius: 6px; }}
QTabBar::tab {{ background: {COLOR_SURFACE}; color: {COLOR_MUTED}; padding: 8px 14px; }}
QTabBar::tab:selected {{ background: {COLOR_SURFACE_RAISED}; color: {COLOR_TEXT}; }}
"""
