import sys
import threading
import time
import json
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from PyQt5 import QtWidgets, QtGui, QtCore
from canvasapi import Canvas
from win10toast import ToastNotifier

# Canvas configuration
API_URL = 'https://canvas.asu.edu'
TOKEN_FILE = 'config.json'

def get_api_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f:
            data = json.load(f)
            return data.get('API_KEY')
    else:
        token, ok = QtWidgets.QInputDialog.getText(None, 'API Token Required',
                                                   'Please enter your Canvas API access token:')
        if ok and token:
            with open(TOKEN_FILE, 'w') as f:
                json.dump({'API_KEY': token}, f)
            return token
        else:
            QtWidgets.QMessageBox.warning(None, 'Token Required', 'An API token is required to proceed.')
            sys.exit()

class DataFetcher(QtCore.QThread):
    data_fetched = QtCore.pyqtSignal(dict, dict)

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas

    def run(self):
        try:
            # Include 'term' and 'syllabus_body' to get more course information
            courses = self.canvas.get_courses(enrollment_state='active', include=['term'])
            assignments_by_course = {}
            grades_by_course = {}
            user_id = self.canvas.get_current_user().id
            now = datetime.now(timezone.utc)
            five_months_ago = now - timedelta(days=150)
            three_months_future = now + timedelta(days=90)

            for course in courses:
                # Get course start and end dates
                start_at = None
                end_at = None

                if hasattr(course, 'start_at') and course.start_at:
                    start_at = datetime.strptime(course.start_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                if hasattr(course, 'end_at') and course.end_at:
                    end_at = datetime.strptime(course.end_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)

                # Filter courses based on start_at and end_at
                if start_at and start_at < five_months_ago:
                    continue
                if end_at and end_at < now:
                    continue

                # Get assignments
                assignments = course.get_assignments(order_by='due_at')
                assignment_list = []
                has_recent_assignments = False
                for assignment in assignments:
                    if assignment.due_at:
                        due_date = datetime.strptime(assignment.due_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                        if now <= due_date <= three_months_future:
                            has_recent_assignments = True
                            # Check submission status
                            try:
                                submission = assignment.get_submission(user_id)
                                submitted = submission.submitted_at is not None
                            except Exception:
                                submitted = False  # Assume not submitted if error occurs
                            points_possible = assignment.points_possible or 0
                            assignment_list.append({
                                'course': course.name,
                                'name': assignment.name,
                                'due_at': due_date,
                                'submitted': submitted,
                                'points': points_possible,
                                'assignment_obj': assignment,
                                'url': assignment.html_url
                            })
                # Skip courses with no recent assignments
                if not assignment_list and not has_recent_assignments:
                    continue
                assignments_by_course[course.name] = assignment_list

                # Get current grade
                try:
                    enrollments = course.get_enrollments(type=['student'])
                    for enrollment in enrollments:
                        if enrollment.user_id == user_id:
                            current_grade = enrollment.grades.get('current_score', 'N/A')
                            grades_by_course[course.name] = current_grade
                            break
                except Exception:
                    grades_by_course[course.name] = 'N/A'

            self.data_fetched.emit(assignments_by_course, grades_by_course)
        except Exception as e:
            print(f"Error fetching data: {e}")
            self.data_fetched.emit({}, {})

class CanvasApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.api_key = get_api_token()
        self.canvas = Canvas(API_URL, self.api_key)
        self.notifier = ToastNotifier()
        self.notification_times = [12]  # Default notification times in hours
        self.assignments_by_course = {}
        self.grades_by_course = {}
        self.init_ui()
        self.start_data_thread()

    def init_ui(self):
        self.setWindowTitle('Canvas Assignment Tracker')
        self.setGeometry(100, 100, 1200, 800)
        self.setWindowIcon(QtGui.QIcon('icon.png'))  # Optional: set your own window icon

        # Use Fusion style for a modern look
        QtWidgets.QApplication.setStyle(QtWidgets.QStyleFactory.create('Fusion'))

        # Main widget and layout
        self.main_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_widget.setLayout(self.main_layout)

        # Left sidebar for courses
        self.course_list = QtWidgets.QListWidget()
        self.course_list.setFixedWidth(200)
        self.course_list.itemClicked.connect(self.on_course_selected)
        self.main_layout.addWidget(self.course_list)

        # Right area for tabs
        self.right_widget = QtWidgets.QWidget()
        self.right_layout = QtWidgets.QVBoxLayout()
        self.right_widget.setLayout(self.right_layout)
        self.main_layout.addWidget(self.right_widget)

        # Tabs for Assignments and Grades
        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.currentChanged.connect(self.on_tab_changed)
        self.right_layout.addWidget(self.tab_widget)

        # Assignments tab
        self.assignments_tab = QtWidgets.QWidget()
        self.assignments_layout = QtWidgets.QVBoxLayout()
        self.assignments_tab.setLayout(self.assignments_layout)
        self.tab_widget.addTab(self.assignments_tab, "Assignments")

        # Grades tab
        self.grades_tab = QtWidgets.QWidget()
        self.grades_layout = QtWidgets.QVBoxLayout()
        self.grades_tab.setLayout(self.grades_layout)
        self.tab_widget.addTab(self.grades_tab, "Grades")

        # Status Bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage('Loading assignments...')

        # Apply stylesheets for a modern look
        self.apply_styles()
        self.show()

    def apply_styles(self):
        # Set the style for the application
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QListWidget {
                background-color: #3c3f41;
                border: none;
                color: #ffffff;
            }
            QTabWidget::pane {
                border: 1px solid #444444;
                background: #3c3f41;
            }
            QTabBar::tab {
                background: #3c3f41;
                padding: 10px;
                color: #aaaaaa;
            }
            QTabBar::tab:selected {
                background: #2b2b2b;
                color: #ffffff;
            }
            QListWidget::item {
                height: 50px;
            }
            QComboBox {
                background-color: #3c3f41;
                color: #ffffff;
                border: 1px solid #555555;
            }
            QPushButton {
                background-color: #0078d7;
                color: #ffffff;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QStatusBar {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QListWidget::item:selected {
                background-color: #005a9e;
            }
        """)

    def start_data_thread(self):
        self.data_fetcher = DataFetcher(self.canvas)
        self.data_fetcher.data_fetched.connect(self.on_data_fetched)
        self.data_fetcher.start()

    def on_data_fetched(self, assignments_by_course, grades_by_course):
        self.assignments_by_course = assignments_by_course
        self.grades_by_course = grades_by_course
        self.populate_courses()
        self.status_bar.showMessage('Assignments loaded.')
        self.start_notification_thread()

    def populate_courses(self):
        self.course_list.clear()
        self.course_list.addItem("All Courses")
        for course_name in sorted(self.assignments_by_course.keys()):
            self.course_list.addItem(course_name)
        # Select "All Courses" by default
        self.course_list.setCurrentRow(0)
        self.on_course_selected(self.course_list.item(0))

    def on_course_selected(self, item):
        course_name = item.text()
        if self.tab_widget.currentIndex() == 0:
            self.show_assignments(course_name)
        else:
            self.show_grades(course_name)

    def on_tab_changed(self, index):
        current_item = self.course_list.currentItem()
        if current_item:
            self.on_course_selected(current_item)

    def show_assignments(self, course_name):
        # Clear existing layout
        for i in reversed(range(self.assignments_layout.count())):
            widget_to_remove = self.assignments_layout.itemAt(i).widget()
            if widget_to_remove is not None:
                self.assignments_layout.removeWidget(widget_to_remove)
                widget_to_remove.setParent(None)

        self.assignment_list_widget = QtWidgets.QListWidget()
        self.assignment_list_widget.setAlternatingRowColors(True)
        self.assignment_list_widget.itemClicked.connect(self.on_assignment_clicked)
        self.assignments_layout.addWidget(self.assignment_list_widget)

        if course_name == "All Courses":
            assignments = []
            for course_assignments in self.assignments_by_course.values():
                assignments.extend(course_assignments)
        else:
            assignments = self.assignments_by_course.get(course_name, [])

        self.populate_assignments(self.assignment_list_widget, assignments)

    def show_grades(self, course_name):
        # Clear existing layout
        for i in reversed(range(self.grades_layout.count())):
            widget_to_remove = self.grades_layout.itemAt(i).widget()
            if widget_to_remove is not None:
                self.grades_layout.removeWidget(widget_to_remove)
                widget_to_remove.setParent(None)

        if course_name == "All Courses":
            grades_widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout()
            grades_widget.setLayout(layout)
            for course, grade in self.grades_by_course.items():
                label = QtWidgets.QLabel(f"{course}: {grade}%")
                layout.addWidget(label)
            self.grades_layout.addWidget(grades_widget)
        else:
            grade = self.grades_by_course.get(course_name, 'N/A')
            grades_widget = QtWidgets.QLabel(f"Current Grade: {grade}%")
            self.grades_layout.addWidget(grades_widget)

    def populate_assignments(self, list_widget, assignments):
        list_widget.clear()
        for assignment in assignments:
            if assignment['submitted']:
                continue  # Skip submitted assignments
            time_remaining = assignment['due_at'] - datetime.now(timezone.utc)
            days = time_remaining.days
            hours, remainder = divmod(time_remaining.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            time_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"
            item_text = f"{assignment['name']} - Due in: {time_str} - Points: {assignment['points']}"
            item = QtWidgets.QListWidgetItem(item_text)
            item.setData(QtCore.Qt.UserRole, assignment)
            list_widget.addItem(item)

    def on_assignment_clicked(self, item):
        assignment = item.data(QtCore.Qt.UserRole)
        webbrowser.open(assignment['url'])

    def start_notification_thread(self):
        self.notification_thread = threading.Thread(target=self.check_due_assignments, daemon=True)
        self.notification_thread.start()

    def check_due_assignments(self):
        notified_assignments = set()
        while True:
            for course_name, assignments in self.assignments_by_course.items():
                for assignment in assignments:
                    if assignment['submitted']:
                        continue  # Skip submitted assignments
                    time_diff = assignment['due_at'] - datetime.now(timezone.utc)
                    hours_remaining = time_diff.total_seconds() / 3600
                    assignment_id = f"{course_name}_{assignment['name']}"
                    for notify_hours in self.notification_times:
                        if 0 < hours_remaining <= notify_hours:
                            key = f"{assignment_id}_{notify_hours}"
                            if key not in notified_assignments:
                                self.send_windows_notification(assignment, hours_remaining)
                                notified_assignments.add(key)
            time.sleep(3600)  # Check every hour
            self.start_data_thread()  # Reload assignments to check for submissions

    def send_windows_notification(self, assignment, hours_remaining):
        notification_title = "Assignment Due Soon!"
        time_str = f"{int(hours_remaining)} hours" if hours_remaining >= 1 else f"{int(hours_remaining * 60)} minutes"
        notification_message = f"{assignment['course']}: {assignment['name']} is due in {time_str}."
        self.notifier.show_toast(notification_title, notification_message, duration=10, threaded=True)

    def closeEvent(self, event):
        # Stop threads when the application is closed
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, notification_times=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.notification_times = notification_times or [1, 3, 6, 12, 24]
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Notification Timing
        layout.addWidget(QtWidgets.QLabel("Notification Timing (hours before due):"))
        self.notification_list = QtWidgets.QListWidget()
        self.notification_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for time_option in [1, 3, 6, 12, 24]:
            item = QtWidgets.QListWidgetItem(f"{time_option} hours")
            item.setData(QtCore.Qt.UserRole, time_option)
            if time_option in self.notification_times:
                item.setSelected(True)
            self.notification_list.addItem(item)
        layout.addWidget(self.notification_list)

        # Save Button
        save_button = QtWidgets.QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

        self.setLayout(layout)

    def save_settings(self):
        self.notification_times = [item.data(QtCore.Qt.UserRole) for item in self.notification_list.selectedItems()]
        self.accept()

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    ex = CanvasApp()
    sys.exit(app.exec_())