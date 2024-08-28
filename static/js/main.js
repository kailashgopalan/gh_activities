// main.js

let currentDate = new Date();
const habits = JSON.parse(document.getElementById('habits-data').textContent);

function changeDate(days) {
    currentDate.setDate(currentDate.getDate() + days);
    document.getElementById('currentDate').textContent = currentDate.toISOString().split('T')[0];
    fetchActivities(currentDate);
}

function fetchActivities(date) {
    fetch(`/activities/${date.toISOString().split('T')[0]}`)
        .then(response => response.json())
        .then(activities => {
            updateActivityLog(activities);
        })
        .catch(error => console.error('Error fetching activities:', error));
}

function updateActivityLog(activities) {
    const activityLog = document.getElementById('activityLog');
    activityLog.innerHTML = '';
    const groupedActivities = groupActivitiesByHabit(activities);
    
    for (const [habit, habitActivities] of Object.entries(groupedActivities)) {
        const habitRow = document.createElement('tr');
        habitRow.className = 'activity-group';
        habitRow.innerHTML = `<td colspan="4"><strong><em>${habit}</em></strong></td>`;
        activityLog.appendChild(habitRow);

        habitActivities.forEach(activity => {
            const activityRow = document.createElement('tr');
            activityRow.innerHTML = `
                <td>${activity.emoji}</td>
                <td>${activity.description}</td>
                <td>${activity.hours}</td>
                <td>
                    <button onclick="showEditForm(${activity.id})">Edit</button>
                    <button onclick="deleteActivity(${activity.id})">Delete</button>
                    <form id="editForm${activity.id}" class="edit-form" onsubmit="return updateActivity(${activity.id})">
                        <select name="habit_id" required>
                            ${habits.map(h => `<option value="${h.id}" ${h.id === activity.habit_id ? 'selected' : ''}>${h.emoji} ${h.name}</option>`).join('')}
                        </select>
                        <input type="text" name="description" value="${activity.description}" required>
                        <input type="number" name="hours" value="${activity.hours}" step="0.1" min="0" required>
                        <button type="submit">Save</button>
                        <button type="button" onclick="hideEditForm(${activity.id})">Cancel</button>
                    </form>
                </td>
            `;
            activityLog.appendChild(activityRow);
        });
    }
}

function groupActivitiesByHabit(activities) {
    return activities.reduce((groups, activity) => {
        const habitName = activity.habit_name;
        if (!groups[habitName]) {
            groups[habitName] = [];
        }
        groups[habitName].push(activity);
        return groups;
    }, {});
}

function showEditForm(activityId) {
    document.getElementById(`editForm${activityId}`).style.display = 'block';
}

function hideEditForm(activityId) {
    document.getElementById(`editForm${activityId}`).style.display = 'none';
}

function updateActivity(activityId) {
    const form = document.getElementById(`editForm${activityId}`);
    const habit_id = form.elements['habit_id'].value;
    const description = form.elements['description'].value;
    const hours = form.elements['hours'].value;

    fetch(`/update_activity/${activityId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ habit_id, description, hours }),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Activity updated successfully');
            fetchActivities(currentDate);
        } else {
            alert('Failed to update activity');
        }
    })
    .catch((error) => {
        console.error('Error:', error);
        alert('An error occurred while updating the activity');
    });

    return false;
}

function deleteActivity(activityId) {
    if (confirm('Are you sure you want to delete this activity?')) {
        fetch(`/delete_activity/${activityId}`, {
            method: 'DELETE',
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('Activity deleted successfully');
                refreshData();
            } else {
                alert('Failed to delete activity: ' + data.message);
            }
        })
        .catch((error) => {
            console.error('Error:', error);
            alert('An error occurred while deleting the activity');
        });
    }
}

function refreshData() {
    Promise.all([
        fetch('/activity_grid_data').then(response => response.json()),
        fetch('/habit_data').then(response => response.json()),
    ])
    .then(([gridData, habitData]) => {
        createActivityGrid(gridData);
        updateCharts(habitData);
        fetchActivities(currentDate);
    })
    .catch(error => console.error('Error refreshing data:', error));
}

function createActivityGrid(gridData) {
    const grid = document.getElementById('activityGrid');
    const tooltip = document.getElementById('tooltip');
    let totalDays = 0;

    grid.innerHTML = '';

    gridData.forEach((day, index) => {
        const cell = document.createElement('div');
        cell.className = 'activity-cell';
        
        if (day.hours > 0) {
            const intensity = Math.min(day.hours / 5, 1);
            cell.style.backgroundColor = `rgba(0, 128, 0, ${intensity})`;
            totalDays++;
        }

        cell.addEventListener('mouseover', (e) => {
            const rect = e.target.getBoundingClientRect();
            const gridRect = grid.getBoundingClientRect();

            let left = rect.left - gridRect.left + grid.scrollLeft;
            let top = rect.bottom - gridRect.top + grid.scrollTop + 5;

            if (left + 220 > gridRect.width) {
                left = gridRect.width - 225;
            }
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${top}px`;

            const habitSummary = day.summary.reduce((acc, activity) => {
                const [habit, hours] = activity.split(': ');
                acc[habit] = (acc[habit] || 0) + parseFloat(hours);
                return acc;
            }, {});

            let tooltipContent = `<strong>${day.date}</strong><br>Total Hours: ${day.hours.toFixed(1)}<br><br>`;
            for (const [habit, hours] of Object.entries(habitSummary)) {
                tooltipContent += `${habit}: ${hours.toFixed(1)} hours<br>`;
            }

            tooltip.innerHTML = tooltipContent;
            tooltip.style.display = 'block';
        });

        cell.addEventListener('mouseout', () => {
            tooltip.style.display = 'none';
        });

        grid.appendChild(cell);
    });

    document.getElementById('totalDays').textContent = totalDays;
}

function updateCharts(habitData) {
    for (const [habitName, data] of Object.entries(habitData)) {
        const chartId = `summaryChart${data.id}`;
        const ctx = document.getElementById(chartId).getContext('2d');
        
        if (window.chartInstances && window.chartInstances[chartId]) {
            window.chartInstances[chartId].destroy();
        }

        window.chartInstances = window.chartInstances || {};
        window.chartInstances[chartId] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.dates,
                datasets: [{
                    label: 'Hours',
                    data: data.hours,
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }
}

function handleAddActivity(e) {
    e.preventDefault();
    const formData = new FormData(this);
    const activityDate = formData.get('activity_date') || new Date().toISOString().split('T')[0];
    
    fetch('/add_activity', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            habit_id: formData.get('habit_id') || null,
            description: formData.get('description'),
            hours: formData.get('hours'),
            date: activityDate
        }),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Activity added successfully');
            this.reset();
            refreshData();
        } else {
            alert('Failed to add activity: ' + data.message);
        }
    })
    .catch((error) => {
        console.error('Error:', error);
        alert('An error occurred while adding the activity');
    });
}

function openChart(evt, chartName) {
    var tabcontent = document.getElementsByClassName("tabcontent");
    for (var i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    var tablinks = document.getElementsByClassName("tablinks");
    for (var i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    document.getElementById(chartName).style.display = "block";
    evt.currentTarget.className += " active";
}

document.addEventListener('DOMContentLoaded', () => {
    refreshData();
    fetchActivities(currentDate);
    document.querySelector('input[name="activity_date"]').value = currentDate.toISOString().split('T')[0];
    document.getElementById('addActivityForm').addEventListener('submit', handleAddActivity);
    document.getElementsByClassName("tablinks")[0].click();
});

// Admin query functionality (if needed)
if (document.getElementById('adminQueryForm')) {
    document.getElementById('adminQueryForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const query = this.elements['query'].value;
        fetch('/admin/query', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ query: query }),
        })
        .then(response => response.json())
        .then(data => {
            const resultDiv = document.getElementById('queryResult');
            if (data.error) {
                resultDiv.innerHTML = `<p style="color: red;">Error: ${data.error}</p>`;
            } else {
                let resultHtml = '<table border="1"><tr>';
                for (let key in data.results[0]) {
                    resultHtml += `<th>${key}</th>`;
                }
                resultHtml += '</tr>';
                data.results.forEach(row => {
                    resultHtml += '<tr>';
                    for (let key in row) {
                        resultHtml += `<td>${row[key]}</td>`;
                    }
                    resultHtml += '</tr>';
                });
                resultHtml += '</table>';
                resultDiv.innerHTML = resultHtml;
            }
        })
        .catch((error) => {
            console.error('Error:', error);
            document.getElementById('queryResult').innerHTML = `<p style="color: red;">An error occurred while running the query</p>`;
        });
    });
}