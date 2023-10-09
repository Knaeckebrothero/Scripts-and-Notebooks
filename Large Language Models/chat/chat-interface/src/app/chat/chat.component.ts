import { Component } from '@angular/core';

@Component({
  selector: 'app-chat',
  templateUrl: './chat.component.html',
  styleUrls: ['./chat.component.scss']
})
export class ChatComponent {
  messages = [
    { content: 'Hello from the other side!', alignment: 'left' },
    { content: 'Hi there!', alignment: 'right' },
    // Add more messages here
  ];
}
