// Google Apps Script web app that creates and deletes Google Calendar events
// for Content Planner Agent.
// Setup: script.google.com -> New project -> paste this code -> change SECRET ->
// Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone).
// Never commit your real SECRET.

const SECRET = "CHANGE-ME";

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const data = JSON.parse(e.postData.contents);
  if (data.token !== SECRET) return out({error: "unauthorized"});
  const cal = CalendarApp.getDefaultCalendar();

  if (data.action === "delete") {
    const ev = cal.getEventById(data.id);
    if (!ev) return out({error: "event not found"});
    ev.deleteEvent();
    return out({ok: true});
  }

  const event = cal.createEvent(
    data.title, new Date(data.start), new Date(data.end),
    {description: data.description}
  );
  event.addPopupReminder(30);
  return out({ok: true, id: event.getId()});
}
