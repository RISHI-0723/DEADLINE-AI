self.addEventListener("push", function(event) {
  const data = event.data.json()
  self.registration.showNotification(data.title, {
    body: data.body,
    icon: "https://cdn-icons-png.flaticon.com/512/2693/2693507.png"
  })
})