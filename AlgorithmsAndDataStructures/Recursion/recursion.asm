section .text
    global recursive_sum

recursive_sum:
    ; Arguments: esi = pointer to array, ecx = length of array
    push    ebp
    mov     ebp, esp
    push    esi
    push    edi
    push    ebx

    ; Base case: if length <= 3, add the numbers and return
    cmp     ecx, 3
    jg      .recursive_case
    xor     eax, eax
    .loop_base_case:
        add     eax, [esi]
        add     esi, 4
        loop    .loop_base_case
        jmp     .end

    .recursive_case:
        ; Allocate space for temporary sums
        lea     edi, [ecx + 2]
        shr     edi, 1
        shl     edi, 2
        sub     esp, edi

        ; Recursive case: divide into groups of three and call recursive_sum
        xor     ebx, ebx
        .loop_recursive_case:
            push    ecx
            push    esi
            mov     ecx, 3
            call    recursive_sum
            pop     esi
            pop     ecx
            add     [esp + ebx * 4], eax
            add     esi, 12
            add     ebx, 1
            sub     ecx, 3
            jg      .loop_recursive_case

        ; Call recursive_sum on the temporary sums
        lea     esi, [esp]
        call    recursive_sum
        add     esp, edi

    .end:
    pop     ebx
    pop     edi
    pop     esi
    mov     esp, ebp
    pop     ebp
    ret
